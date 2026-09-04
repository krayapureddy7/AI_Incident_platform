"""
Workflow checkpointer construction.

A checkpointer is what makes a halted workflow resumable: when a Tier-3 action
stops at ``AWAITING_APPROVAL``, the graph's position and state are written to
the checkpointer, and ``resume_incident`` reads them back.

Two backends are supported:

- **SQLite** (default when a path is supplied) -- durable across process
  restarts, so a run held for approval can be resumed by a different process
  than the one that started it.
- **In-memory** -- used for tests and one-shot runs where durability is
  irrelevant. Chosen automatically if the SQLite extra is not installed.

The SQLite backend is a single-node durability story, not a distributed one. See
``TRADE_OFFS.md`` section 2.1 for the multi-worker hardening path.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Optional

from langgraph.checkpoint.memory import MemorySaver

__all__ = [
    "build_checkpointer",
    "close_checkpointer",
    "DEFAULT_CHECKPOINT_DB",
    "sqlite_checkpointer_available",
]

DEFAULT_CHECKPOINT_DB = os.environ.get("CHECKPOINT_DB_PATH", ".data/checkpoints.db")


def sqlite_checkpointer_available() -> bool:
    """True when the durable SQLite checkpointer backend can be imported."""
    try:
        import langgraph.checkpoint.sqlite  # noqa: F401
    except ImportError:
        return False
    return True


def build_checkpointer(db_path: Optional[str] = None) -> Any:
    """
    Construct a checkpointer.

    Args:
        db_path: SQLite database path for durable checkpoints. ``None`` selects
            the in-memory backend.

    Returns:
        A LangGraph checkpointer. Falls back to ``MemorySaver`` when a durable
        backend is requested but unavailable, so a missing optional dependency
        degrades resumability rather than breaking the platform.
    """
    if not db_path:
        return MemorySaver()

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        return MemorySaver()

    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    # `check_same_thread=False` is required because LangGraph may touch the
    # checkpointer from more than one thread over a run's lifetime. SQLite
    # serializes writes internally, and each orchestrator owns its own
    # connection, so concurrent access is safe here.
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def close_checkpointer(checkpointer: Any) -> None:
    """
    Release any database handle a checkpointer holds.

    The SQLite backend keeps a connection open for the life of the orchestrator.
    Long-lived processes must close it on shutdown, and on Windows an open handle
    also blocks deletion of the underlying file. In-memory checkpointers hold no
    resources, so this is a no-op for them.
    """
    conn = getattr(checkpointer, "conn", None)
    if conn is None:
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001 - closing must never raise on shutdown
        pass
