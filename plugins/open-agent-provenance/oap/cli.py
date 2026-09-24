"""Local administration and export CLI."""

import argparse
import json
import sys

from .graph import export
from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Local W3C PROV capture for Claude Code and Codex")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Run the MCP stdio server")
    sub.add_parser("sessions", help="List the 20 most recent sessions")
    verify = sub.add_parser("verify", help="Check a journal's local integrity")
    verify.add_argument("session")
    exp = sub.add_parser("export", help="Write PROV to stdout")
    exp.add_argument("session")
    exp.add_argument("--format", choices=["json", "provn", "turtle"], default="json")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            from .server import main as serve

            serve()
        elif args.command == "sessions":
            print(json.dumps(Store().sessions(), indent=2))
        elif args.command == "verify":
            result = Store().verify(args.session)
            print(json.dumps(result, indent=2))
            if not result["valid"]:
                raise SystemExit(1)
        else:
            print(export(Store(), args.session, args.format))
    except (ValueError, OSError) as exc:
        print(f"open-agent-provenance: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
