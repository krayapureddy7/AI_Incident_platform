"""
Unified command line interface for the Mini Agentic AI Platform.

Commands:
  demo         Run the end-to-end incident demo
  run          Execute an incident remediation workflow
  approve      Supply a Tier-3 human approval token and resume a held run
  eval         Run the offline evaluation and invariant gate
  replay       Replay a persisted audit trace (by run id or file)
  runs         List persisted incident runs
  rag          Query the Hybrid RAG knowledge base
  blast-radius Inspect service topology and blast radius
  tools        List registered tools and their JSON-Schema contracts
  serve        Start the HTTP API
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

from .demo import run_demo
from .evals.eval_runner import run_cli_eval
from .knowledge.hybrid_rag import GLOBAL_HYBRID_RAG
from .knowledge.knowledge_graph import GLOBAL_KNOWLEDGE_GRAPH
from .models import Incident
from .orchestrator.state_machine import IncidentOrchestrator, OrchestrationException
from .orchestrator.checkpointing import DEFAULT_CHECKPOINT_DB
from .persistence.store import DEFAULT_DB_PATH, IncidentStore
from .safety.approval import (
    DEFAULT_TTL_SECONDS as APPROVAL_DEFAULT_TTL_SECONDS,
    ApprovalTokenError,
    mint_approval_token,
)
from .tools.mcp_server import TOOL_SERVER
from .tracing.tracer import TraceReplayer


def _emit(payload: Any) -> None:
    """Print a JSON payload with non-serializable values coerced to strings."""
    print(json.dumps(payload, indent=2, default=str))


def _summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """Project a run result down to the fields worth showing on a terminal."""
    return {
        "run_id": result.get("run_id"),
        "status": result.get("status"),
        "final_state": result.get("final_state"),
        "reason": result.get("reason"),
        "proposal": result.get("proposal"),
        "safety_result": result.get("safety_result"),
        "execution_result": result.get("execution_result"),
        "recovery_verification": result.get("recovery_verification"),
        "trace_file": result.get("trace_file"),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mini-agent-platform",
        description="Mini Agentic AI Platform for production incident remediation",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path for run persistence (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--checkpoint-db",
        default=DEFAULT_CHECKPOINT_DB,
        help=(
            "SQLite path for durable workflow checkpoints, which is what makes a "
            f"held run resumable across processes (default: {DEFAULT_CHECKPOINT_DB})"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    subparsers.add_parser("demo", help="Run the guided end-to-end incident demo")
    subparsers.add_parser("eval", help="Run the trajectory and invariant evaluation gate")
    subparsers.add_parser("tools", help="List registered tools and parameter schemas")

    run_parser = subparsers.add_parser("run", help="Run incident remediation")
    run_parser.add_argument("--service", required=True, help="Target service (e.g. payment-service)")
    run_parser.add_argument("--desc", required=True, help="Incident description / alert text")
    run_parser.add_argument("--title", default=None, help="Incident title")
    run_parser.add_argument("--env", default="prod", help="Target environment")
    run_parser.add_argument("--tenant", default="default", help="Target tenant")
    run_parser.add_argument(
        "--env-context",
        default=None,
        help="Session environment authority (defaults to --env). Set differently to "
        "exercise cross-environment rejection.",
    )
    run_parser.add_argument(
        "--tenant-context",
        default=None,
        help="Session tenant authority (defaults to --tenant).",
    )
    run_parser.add_argument("--severity", default="SEV-1", help="Incident severity")
    run_parser.add_argument("--token", default=None, help="Tier-3 human approval token")
    run_parser.add_argument("--run-id", default=None, help="Explicit run id")

    approve_parser = subparsers.add_parser(
        "approve", help="Resume a held Tier-3 run with a human approval token"
    )
    approve_parser.add_argument("--run-id", required=True, help="Run id to resume")
    approve_parser.add_argument(
        "--token",
        default=None,
        help="Signed human approval token. Omit and pass --approver to mint one here.",
    )
    approve_parser.add_argument(
        "--approver",
        default=None,
        help=(
            "Mint an approval for the held proposal as this identity, instead of "
            "supplying --token. Requires the signing secret in this process."
        ),
    )
    approve_parser.add_argument(
        "--ttl",
        type=int,
        default=APPROVAL_DEFAULT_TTL_SECONDS,
        help=f"Validity window in seconds for a minted token (default {APPROVAL_DEFAULT_TTL_SECONDS})",
    )

    replay_parser = subparsers.add_parser("replay", help="Replay a persisted audit trace")
    replay_group = replay_parser.add_mutually_exclusive_group(required=True)
    replay_group.add_argument("--run-id", help="Run id to replay from the durable store")
    replay_group.add_argument("--trace-file", help="Path to a trace JSON file")

    runs_parser = subparsers.add_parser("runs", help="List persisted incident runs")
    runs_parser.add_argument("--limit", type=int, default=20, help="Maximum rows to return")
    runs_parser.add_argument("--service", default=None, help="Filter by service")
    runs_parser.add_argument("--status", default=None, help="Filter by status")

    rag_parser = subparsers.add_parser("rag", help="Query the Hybrid RAG knowledge base")
    rag_parser.add_argument("--query", required=True, help="Search query string")
    rag_parser.add_argument("--service", default=None, help="Service metadata filter")
    rag_parser.add_argument("--env", default=None, help="Environment metadata filter")
    rag_parser.add_argument("--type", dest="doc_type", default=None, help="Document type filter")
    rag_parser.add_argument("--top-k", type=int, default=3, help="Results to return")

    blast_parser = subparsers.add_parser(
        "blast-radius", help="Calculate blast radius for a service"
    )
    blast_parser.add_argument("--service", required=True, help="Service name")

    serve_parser = subparsers.add_parser("serve", help="Start the HTTP API")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    serve_parser.add_argument("--port", type=int, default=8000, help="Bind port")
    serve_parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    return parser


def _mint_for_held_run(
    orchestrator: IncidentOrchestrator, run_id: str, approver: str, ttl: int
) -> Optional[str]:
    """
    Mint an approval bound to the proposal a held run is actually waiting on.

    The operator approves a concrete action, not a run id, so the proposal is
    read back from the checkpoint and signed as-is. Minting happens here rather
    than behind an API endpoint because the signing secret belongs to the
    approver's environment -- an endpoint that minted on request would hand out
    the very authority the token exists to prove.
    """
    snapshot = orchestrator.get_checkpoint_state(run_id)
    if not snapshot:
        print(f"No active checkpoint for run '{run_id}'.", file=sys.stderr)
        return None

    proposal = snapshot.get("values", {}).get("proposal")
    if not proposal:
        print(f"Run '{run_id}' has no proposal awaiting approval.", file=sys.stderr)
        return None

    try:
        token = mint_approval_token(proposal, approver=approver, ttl_seconds=ttl)
    except ApprovalTokenError as exc:
        print(f"Cannot mint an approval for run '{run_id}': {exc}", file=sys.stderr)
        return None

    print(
        f"Minted approval for {proposal.get('tool_name')} on "
        f"{proposal.get('service')} (action {proposal.get('action_id')}), "
        f"valid {ttl}s, approver '{approver}'.",
        file=sys.stderr,
    )
    return token


def _orchestrator(args: argparse.Namespace) -> IncidentOrchestrator:
    """Build an orchestrator backed by the durable store and checkpointer."""
    return IncidentOrchestrator(
        store=IncidentStore(db_path=args.db),
        checkpoint_db=args.checkpoint_db,
    )


def main(argv: Optional[list] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "demo":
        result = run_demo()
        return 0 if result.get("status") == "RESOLVED" else 1

    if args.command == "eval":
        report = run_cli_eval()
        return 1 if report["failed"] else 0

    if args.command == "tools":
        _emit(TOOL_SERVER.tool_catalog())
        return 0

    if args.command == "run":
        incident = Incident(
            id=args.run_id or f"CLI-{args.service.upper()}",
            title=args.title or f"CLI incident on {args.service}",
            description=args.desc,
            service=args.service,
            environment=args.env,
            tenant=args.tenant,
            severity=args.severity,
        )
        result = _orchestrator(args).run_incident(
            incident=incident,
            human_approval_token=args.token,
            env_context=args.env_context or args.env,
            tenant_context=args.tenant_context or args.tenant,
            run_id=args.run_id,
        )
        _emit(_summary(result))
        return 0 if result.get("status") in ("RESOLVED", "BLOCKED_FOR_APPROVAL") else 1

    if args.command == "approve":
        # Durable checkpoints make this work across processes; see
        # TRADE_OFFS.md section 2.1 for the distributed hardening path.
        orchestrator = _orchestrator(args)

        token = args.token
        if not token:
            if not args.approver:
                print(
                    "Supply either --token, or --approver to mint one for the held action.",
                    file=sys.stderr,
                )
                return 2
            token = _mint_for_held_run(orchestrator, args.run_id, args.approver, args.ttl)
            if token is None:
                return 2

        try:
            result = orchestrator.resume_incident(
                run_id=args.run_id, human_approval_token=token
            )
        except OrchestrationException as exc:
            print(f"Cannot resume '{args.run_id}': {exc}", file=sys.stderr)
            print(
                f"Checked checkpoint store '{args.checkpoint_db}'. The run id may be "
                "wrong, or the run was executed with in-memory checkpointing.",
                file=sys.stderr,
            )
            return 2
        _emit(_summary(result))
        return 0 if result.get("status") == "RESOLVED" else 1

    if args.command == "replay":
        if args.run_id:
            trace = IncidentStore(db_path=args.db).get_trace(args.run_id)
            if not trace:
                print(f"No persisted trace for run '{args.run_id}'.", file=sys.stderr)
                return 2
        else:
            trace = TraceReplayer.load_trace(args.trace_file)
        print(TraceReplayer.replay_summary(trace))
        return 0

    if args.command == "runs":
        runs = IncidentStore(db_path=args.db).list_runs(
            limit=args.limit, service=args.service, status=args.status
        )
        _emit(
            [
                {
                    "run_id": r["run_id"],
                    "service": r["service"],
                    "environment": r["environment"],
                    "status": r["status"],
                    "final_state": r["final_state"],
                }
                for r in runs
            ]
        )
        return 0

    if args.command == "rag":
        _emit(
            GLOBAL_HYBRID_RAG.search(
                args.query,
                service_filter=args.service,
                env_filter=args.env,
                type_filter=args.doc_type,
                top_k=args.top_k,
            )
        )
        return 0

    if args.command == "blast-radius":
        analysis = GLOBAL_KNOWLEDGE_GRAPH.calculate_blast_radius(args.service)
        _emit(analysis)
        return 0 if analysis.get("found") else 2

    if args.command == "serve":
        try:
            import uvicorn
        except ImportError:
            print("uvicorn is required for `serve`. Install with: pip install uvicorn", file=sys.stderr)
            return 2
        uvicorn.run(
            "mini_platform.api.server:app", host=args.host, port=args.port, reload=args.reload
        )
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
