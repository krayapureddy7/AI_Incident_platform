# Demo Script

A walkthrough covering planning, retrieval, verification, action, and trace
review — plus the cases where the platform *refuses* to act, which are the
interesting ones for a system that touches production.

Setup:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Act 1 — The happy path: autonomous Tier-2 remediation

```bash
python -m mini_platform.demo
```

Payment-service has a heap leak. Blast radius is 4 affected services, none
tier-0, so the action classifies as Tier-2 and executes without a human.

Watch for, in order:

1. **Planning** — three symptoms extracted from the alert text, four diagnostic
   tasks delegated. The A2A envelope header shows `orchestrator → planner`.
2. **Retrieval** — telemetry read *through the Tool Gateway*, then two cited
   runbooks with per-ranker scores (`RRF`, `BM25`, `dense`). `DOC-RB-PAY-001`
   should rank first.
3. **Blast radius** — BFS finds 2 direct and 2 transitive dependents.
4. **Verification** — four deterministic policy checks, all passing, tier 2.
   Note the *rejected alternatives* the Ops agent recorded: scaling a leaking pod
   multiplies the leak.
5. **Action** — `simulate_restart@1.2.0` dispatched. `Gateway dispatches:
   total=6, mutating=1` — six calls crossed the boundary, exactly one mutated.
6. **Recovery** — before/after deltas, and `Log health: 5 lines read,
   fatal=False`. The line count matters: it proves the log gate read real data.
7. **Trace** — step count, A2A count, and a full replay reconstructed from the
   persisted trace.

---

## Act 2 — Refusal: Tier-0 requires a human

```bash
python -m mini_platform.cli run --service auth-service \
  --desc "Redis connection timeout, CPU 94%, thread starvation" \
  --run-id DEMO-AUTH-1
```

Auth-service is tier-0 with 6 affected services. Expected:

```
"status": "BLOCKED_FOR_APPROVAL",
"final_state": "AWAITING_APPROVAL",
"reason": "BLOCKED FOR HUMAN APPROVAL: Action classified as TIER_3_HUMAN_APPROVAL ..."
```

`execution_result` is `null`. Nothing was touched.

### Then approve it — from a different process

```bash
python -m mini_platform.cli approve --run-id DEMO-AUTH-1 \
  --token TOKEN-HUMAN-APPROVED-SRE-001
```

This is a fresh process with a fresh orchestrator. It resumes from the durable
checkpoint, re-runs the Verifier (the proposal is re-adjudicated, not executed on
trust), and completes. Expected: `"status": "RESOLVED"`.

---

## Act 3 — Refusal: cross-environment is a hard rejection

```bash
python -m mini_platform.cli run --service payment-service \
  --desc "staging agent reaching into prod" \
  --env prod --env-context staging
```

The incident targets `prod`; the session's authority is `staging`. Expected:

```
"status": "REJECTED",
"final_state": "FAILED",
"policy_violations": ["CROSS_ENVIRONMENT_VIOLATION: ..."]
```

Two things to point out:

- `requires_human_token` is **false**. This is not escalated for approval — no
  token can authorize it.
- The Investigator's evidence is empty and `tool_errors` records
  `ENVIRONMENT_MISMATCH`. Reads are session-scoped, so the staging session could
  not even *observe* production. The gap is visible in the trace rather than
  silently filled.

---

## Act 4 — Refusal: privilege escalation

The Investigator is read-only. Prove it cannot mutate, by either route:

```bash
python - <<'EOF'
from tests.conftest import build_isolated_stack
cluster, impl, gw = build_isolated_stack()

for route, call in [
    ("read-only path",  lambda: gw.execute_read_only("simulate_restart", "auth-service",
                                                     caller_role="investigator")),
    ("proposal path",   lambda: gw.execute_proposal(
        {"tool_name": "simulate_restart", "service": "auth-service",
         "environment": "prod", "parameters": {}}, caller_role="investigator")),
]:
    r = call()
    print(f"{route:16} ok={r['ok']}  code={r['error']['code']}")

print("mutating dispatches:", gw.mutating_call_count)
print("cluster audit log  :", len(cluster.execution_audit_log))
print("auth-service status:", cluster.get_service("auth-service")["status"])
EOF
```

Expected: both denied with `POLICY_DENIED`, zero mutating dispatches, empty audit
log, service still `DEGRADED`.

---

## Act 5 — Evaluation gate

```bash
python -m mini_platform.evals.eval_runner
echo "exit code: $?"
```

Eight scenarios: correct remediation trajectory, Tier-0 human gate,
cross-environment isolation, cross-tenant isolation, blast-radius governance,
unapproved Tier-3 block, idempotency, and approved Tier-0 execution.

Every non-executing scenario also asserts the cross-cutting invariant *no
mutating tool was dispatched*. Exit code 0 on pass, 1 on any failure — this is
the CI gate.

---

## Act 6 — Trace review and replay

```bash
python -m mini_platform.cli runs
python -m mini_platform.cli replay --run-id DEMO-AUTH-1
```

The replay is reconstructed from SQLite, not from memory — a different process,
after the original has exited. It shows the state-transition timeline (including
the approval hold and the resume), per-step decisions and rejected alternatives,
and the A2A message flow.

Confirm redaction is applied before persistence:

```bash
python - <<'EOF'
from mini_platform.tracing.tracer import redact_sensitive_data
print(redact_sensitive_data({
    "api_key": "sk-live-123", "db_password": "hunter2",
    "email": "sre@corp.internal", "card": "4532-1234-5678-9010",
    "service": "payment-service",
}))
EOF
```

---

## Act 7 — Resilience

```bash
pytest tests/test_orchestrator_resilience.py -v
```

Covers: transient failure retried to success; retry budget exhausted reported as
`ORCHESTRATION_FAILED` with the failing node named; a hung node abandoned by
timeout; and — most importantly — **the executor is never auto-retried**, because
re-issuing a mutation of unknown outcome is unsafe.

---

## Act 8 — HTTP API

```bash
python -m mini_platform.cli serve --port 8000
```

```bash
# Tier-2: resolves autonomously
curl -sS -X POST localhost:8000/incidents -H 'content-type: application/json' -d '{
  "title": "Payment heap saturation",
  "description": "payment-service OOMKilled, memory at 96.2%",
  "service": "payment-service"
}' | python -m json.tool | head -20

# Tier-0: halts, then approve
RUN=$(curl -sS -X POST localhost:8000/incidents -H 'content-type: application/json' \
  -d '{"title":"Auth saturation","description":"auth-service CPU 94% redis timeouts","service":"auth-service"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')

curl -sS -X POST "localhost:8000/incidents/$RUN/approve" \
  -H 'content-type: application/json' \
  -d '{"human_approval_token":"TOKEN-HUMAN-APPROVED-API-1"}' | python -m json.tool | head -8

curl -sS "localhost:8000/traces/$RUN/replay" | python -m json.tool
```

Interactive docs at `http://localhost:8000/docs`.

---

## Act 9 — Versioning and rollback

```bash
python -m tools.check_versions
```

Then demonstrate the gate catching drift:

```bash
python - <<'EOF'
import json, pathlib
p = pathlib.Path("VERSION.json"); original = p.read_text()
d = json.loads(original); d["components"]["agents"]["planner"] = "9.9.9"
p.write_text(json.dumps(d, indent=2))
EOF

python -m tools.check_versions; echo "exit code: $?"   # 1, drift reported
git checkout VERSION.json 2>/dev/null || true
```

A behaviour change cannot merge without a version bump, so every deployment has
a named rollback target. The trace records which versions produced a given run.

---

## Full pipeline

```bash
./run_ci.sh
```

Tests → eval gate → demo sanity check → CLI smoke checks.
