"""
Durable incident and audit persistence.

Backed by SQLite via the standard library, so runs survive process restarts and
remain replayable without any external service. Two tables are maintained:

- ``incident_runs``  -- one row per workflow run: incident, scope, outcome.
- ``audit_traces``   -- the redacted trace JSON emitted by ``AuditTracer``.

Scope note
----------
This store persists *completed and halted run records* so they can be queried
and replayed later. It is not the LangGraph checkpointer: resuming a workflow
that is mid-flight still requires the in-process checkpointer (see
``TRADE_OFFS.md`` section 2.1 for the durable-execution hardening path).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

DEFAULT_DB_PATH = os.environ.get("SQLITE_DB_PATH", ".data/incident_platform.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incident_runs (
    run_id           TEXT PRIMARY KEY,
    incident_id      TEXT NOT NULL,
    title            TEXT,
    service          TEXT NOT NULL,
    environment      TEXT NOT NULL,
    tenant           TEXT NOT NULL,
    severity         TEXT,
    status           TEXT NOT NULL,
    final_state      TEXT NOT NULL,
    reason           TEXT,
    proposal_json    TEXT,
    safety_json      TEXT,
    execution_json   TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incident_runs_service  ON incident_runs (service);
CREATE INDEX IF NOT EXISTS idx_incident_runs_status   ON incident_runs (status);
CREATE INDEX IF NOT EXISTS idx_incident_runs_created  ON incident_runs (created_at DESC);

CREATE TABLE IF NOT EXISTS audit_traces (
    run_id      TEXT PRIMARY KEY,
    trace_json  TEXT NOT NULL,
    step_count  INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    stored_at   REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES incident_runs (run_id) ON DELETE CASCADE
);
"""


class IncidentStore:
    """
    SQLite-backed catalogue of incident runs and their audit traces.

    Instances are safe to share across threads: every operation acquires a lock
    and uses a short-lived connection, which keeps the write path simple and
    avoids cross-thread connection reuse.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._ensure_parent_dir()
        self._initialise_schema()

    # -- Connection management ---------------------------------------------
    def _ensure_parent_dir(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialise_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    # -- Writes -------------------------------------------------------------
    def record_run(self, run_id: str, incident: Dict[str, Any], result: Dict[str, Any]) -> None:
        """
        Upsert the outcome of a workflow run.

        Called once per run (and again on resume), so a Tier-3 hold followed by
        an approved resume leaves a single row reflecting the final outcome.
        """
        now = time.time()
        payload = (
            run_id,
            incident.get("id", "UNKNOWN"),
            incident.get("title"),
            incident.get("service", "unknown"),
            incident.get("environment", "prod"),
            incident.get("tenant", "default"),
            incident.get("severity"),
            result.get("status", "UNKNOWN"),
            result.get("final_state", "UNKNOWN"),
            result.get("reason"),
            _dumps(result.get("proposal")),
            _dumps(result.get("safety_result")),
            _dumps(result.get("execution_result")),
            now,
            now,
        )

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO incident_runs (
                    run_id, incident_id, title, service, environment, tenant, severity,
                    status, final_state, reason, proposal_json, safety_json,
                    execution_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status         = excluded.status,
                    final_state    = excluded.final_state,
                    reason         = excluded.reason,
                    proposal_json  = excluded.proposal_json,
                    safety_json    = excluded.safety_json,
                    execution_json = excluded.execution_json,
                    updated_at     = excluded.updated_at
                """,
                payload,
            )

    def record_trace(self, run_id: str, trace: Dict[str, Any]) -> None:
        """Persist (or replace) the redacted audit trace for a run."""
        if not trace:
            return

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_traces (
                    run_id, trace_json, step_count, total_tokens, total_cost_usd, stored_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    trace_json     = excluded.trace_json,
                    step_count     = excluded.step_count,
                    total_tokens   = excluded.total_tokens,
                    total_cost_usd = excluded.total_cost_usd,
                    stored_at      = excluded.stored_at
                """,
                (
                    run_id,
                    json.dumps(trace, default=str),
                    trace.get("step_count", 0),
                    trace.get("total_tokens", 0),
                    trace.get("total_cost_usd", 0.0),
                    time.time(),
                ),
            )

    # -- Reads --------------------------------------------------------------
    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single run record, or None when the run is unknown."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM incident_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _row_to_run(row) if row else None

    def get_trace(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the persisted audit trace for a run, or None."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT trace_json FROM audit_traces WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            return None
        return json.loads(row["trace_json"])

    def list_runs(
        self,
        limit: int = 50,
        service: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List recent runs, newest first, with optional service/status filters."""
        clauses: List[str] = []
        params: List[Any] = []
        if service:
            clauses.append("service = ?")
            params.append(service)
        if status:
            clauses.append("status = ?")
            params.append(status)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM incident_runs {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_row_to_run(row) for row in rows]

    def count_runs(self) -> int:
        """Total number of persisted runs."""
        with self._lock, self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM incident_runs").fetchone()["n"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dumps(value: Any) -> Optional[str]:
    """JSON-encode a nullable payload for column storage."""
    if value is None:
        return None
    return json.dumps(value, default=str)


def _loads(value: Optional[str]) -> Any:
    """Decode a nullable JSON column."""
    if not value:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _row_to_run(row: sqlite3.Row) -> Dict[str, Any]:
    """Map an ``incident_runs`` row to the public dict representation."""
    return {
        "run_id": row["run_id"],
        "incident_id": row["incident_id"],
        "title": row["title"],
        "service": row["service"],
        "environment": row["environment"],
        "tenant": row["tenant"],
        "severity": row["severity"],
        "status": row["status"],
        "final_state": row["final_state"],
        "reason": row["reason"],
        "proposal": _loads(row["proposal_json"]),
        "safety_result": _loads(row["safety_json"]),
        "execution_result": _loads(row["execution_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
