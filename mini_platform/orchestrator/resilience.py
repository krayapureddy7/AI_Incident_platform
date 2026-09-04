"""
Node-level retry and timeout enforcement for the LangGraph workflow.

Every graph node is wrapped in a resilience guard before registration. The guard
provides two things the orchestration layer requires:

- **Timeout**: each node body runs in a worker thread and is abandoned if it
  exceeds ``timeout_sec``. A hung tool call cannot stall the workflow forever.
- **Retry**: transient failures are retried with exponential backoff up to
  ``max_retries`` times.

Retry safety
------------
Retries apply only to nodes explicitly marked ``retryable``. Mutating nodes (the
executor) are wrapped with a timeout but **never** retried automatically, since
re-issuing a mutation whose outcome is unknown is unsafe. The Tool Gateway's
idempotency window is a second line of defence, not a licence to retry.

Only *exceptions* trigger a retry. A deterministic policy rejection is a normal
return value, so the Verifier's decisions are never retried into a different
outcome -- workflow control stays deterministic.
"""
from __future__ import annotations

import concurrent.futures
import time
from typing import Any, Callable, Dict, Optional

__all__ = ["NodeExecutionError", "NodeTimeoutError", "with_resilience"]


class NodeExecutionError(Exception):
    """Raised when a workflow node exhausts its retry budget."""

    def __init__(self, node: str, attempts: int, last_error: BaseException):
        super().__init__(
            f"Node '{node}' failed after {attempts} attempt(s): "
            f"{type(last_error).__name__}: {last_error}"
        )
        self.node = node
        self.attempts = attempts
        self.last_error = last_error


class NodeTimeoutError(TimeoutError):
    """Raised when a workflow node exceeds its allotted wall-clock budget."""

    def __init__(self, node: str, timeout_sec: float):
        super().__init__(f"Node '{node}' exceeded its {timeout_sec}s execution budget.")
        self.node = node
        self.timeout_sec = timeout_sec


def with_resilience(
    fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    node: str,
    timeout_sec: float,
    max_retries: int = 0,
    backoff_base_sec: float = 0.05,
    tracer_resolver: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """
    Wrap a LangGraph node function with timeout and optional retry handling.

    Args:
        fn: The node body, taking and returning graph state.
        node: Node name, used in errors and trace records.
        timeout_sec: Per-attempt wall-clock budget.
        max_retries: Additional attempts after the first. ``0`` disables retry,
            which is required for nodes that mutate infrastructure.
        backoff_base_sec: Base delay for exponential backoff between attempts.
        tracer_resolver: Optional callable resolving an ``AuditTracer`` from
            state, so retries and timeouts are recorded in the audit trail.

    Returns:
        A drop-in replacement node function.
    """
    total_attempts = max(1, max_retries + 1)

    def _record(state: Dict[str, Any], from_state: str, trigger: str, metadata: Dict[str, Any]) -> None:
        if tracer_resolver is None:
            return
        tracer = tracer_resolver(state)
        if tracer is None:
            return
        tracer.record_transition(from_state, from_state, trigger, metadata)

    def wrapped(state: Dict[str, Any]) -> Dict[str, Any]:
        last_error: Optional[BaseException] = None
        current_state = state.get("current_state", "UNKNOWN")

        for attempt in range(1, total_attempts + 1):
            started = time.time()
            # The pool is deliberately not used as a context manager: `__exit__`
            # calls shutdown(wait=True), which would block on the very worker the
            # timeout exists to abandon. Instead the pool is shut down without
            # waiting. Python cannot kill a running thread, so a stalled worker
            # lingers until it returns on its own -- but it no longer holds up the
            # workflow, which is the property the timeout must guarantee.
            pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=f"node-{node}"
            )
            try:
                future = pool.submit(fn, state)
                try:
                    result = future.result(timeout=timeout_sec)
                except concurrent.futures.TimeoutError as exc:
                    future.cancel()
                    raise NodeTimeoutError(node, timeout_sec) from exc
                else:
                    pool.shutdown(wait=False)
                    return result
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                pool.shutdown(wait=False)
                last_error = exc
                elapsed_ms = round((time.time() - started) * 1000.0, 2)
                is_final = attempt >= total_attempts

                _record(
                    state,
                    current_state,
                    "NODE_TIMEOUT" if isinstance(exc, NodeTimeoutError) else "NODE_RETRY",
                    {
                        "node": node,
                        "attempt": attempt,
                        "max_attempts": total_attempts,
                        "elapsed_ms": elapsed_ms,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "will_retry": not is_final,
                    },
                )

                if is_final:
                    raise NodeExecutionError(node, attempt, exc) from exc

                time.sleep(backoff_base_sec * (2 ** (attempt - 1)))

        # Unreachable: the loop either returns or raises.
        raise NodeExecutionError(node, total_attempts, last_error or RuntimeError("unknown"))

    wrapped.__name__ = getattr(fn, "__name__", node)
    wrapped.__doc__ = fn.__doc__
    return wrapped
