"""
Standalone entry point for the FastMCP tool server.

Serves the versioned tool catalog over an MCP transport so the tool plane can be
deployed as its own process or container, separate from the API.

Security note
-------------
This process performs argument and resource validation only. It does **not**
enforce the agent permission matrix, autonomy tiers, blast-radius limits, or
idempotency -- those live in the Tool Gateway. A deployment must therefore keep
this listener on an internal network reachable only by the gateway. Exposing it
directly would bypass every policy check.

Usage:
    python -m app.tools.serve_mcp --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import argparse
import sys

from .server import fastmcp_server


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.tools.serve_mcp", description="Run the FastMCP incident tool server."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8080, help="Bind port")
    parser.add_argument(
        "--transport",
        default="sse",
        choices=["sse", "stdio", "streamable-http"],
        help="MCP transport to serve (default: sse)",
    )
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        fastmcp_server.run(transport="stdio")
        return 0

    fastmcp_server.run(transport=args.transport, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
