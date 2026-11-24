#!/usr/bin/env python3
"""Automated route table generator.

Inspects app.openapi() and test_auth_and_roles.POLICY_TABLE to generate a
provably complete Markdown table of every /api endpoint and its RBAC access policy.
Must NOT modify database records or bypass security policies.
"""
import sys
from pathlib import Path

# Ensure server/ is on sys.path
root_dir = Path(__file__).resolve().parent.parent
server_dir = root_dir / "server"
if str(server_dir) not in sys.path:
    sys.path.insert(0, str(server_dir))

from app.main import app
from tests.test_auth_and_roles import POLICY_TABLE

POLICY_MATRIX = {
    "public": {
        "manager": "Allowed",
        "safety_officer": "Allowed",
        "engineer": "Allowed",
        "foreman": "Allowed",
        "miner": "Allowed",
        "anon": "Allowed",
    },
    "any_authenticated": {
        "manager": "Allowed",
        "safety_officer": "Allowed",
        "engineer": "Allowed",
        "foreman": "Allowed",
        "miner": "Allowed",
        "anon": "401",
    },
    "any_role_scoped": {
        "manager": "Scoped",
        "safety_officer": "Scoped",
        "engineer": "Scoped",
        "foreman": "Scoped",
        "miner": "Scoped",
        "anon": "401",
    },
    "scanners": {
        "manager": "Scoped",
        "safety_officer": "Scoped",
        "engineer": "Scoped",
        "foreman": "Own Tunnel",
        "miner": "403",
        "anon": "401",
    },
    "superiors_scoped": {
        "manager": "Scoped",
        "safety_officer": "Scoped",
        "engineer": "Scoped",
        "foreman": "403",
        "miner": "403",
        "anon": "401",
    },
    "manager_only": {
        "manager": "Allowed",
        "safety_officer": "403",
        "engineer": "403",
        "foreman": "403",
        "miner": "403",
        "anon": "401",
    },
    "manager_or_foreman": {
        "manager": "Allowed",
        "safety_officer": "403",
        "engineer": "403",
        "foreman": "Allowed",
        "miner": "403",
        "anon": "401",
    },
}


def generate_markdown_table() -> str:
    openapi = app.openapi()
    paths = openapi.get("paths", {})

    # Build list of routes and extract summaries
    route_entries = []
    for path, path_item in paths.items():
        if not path.startswith("/api"):
            continue
        for method_str, op in path_item.items():
            m = method_str.upper()
            if m in ("HEAD", "OPTIONS"):
                continue
            desc = (op.get("description") or "").strip().split("\n")[0]
            summary = desc or op.get("summary") or ""
            clean_summary = summary.replace("|", "\\|").strip()
            policy = POLICY_TABLE.get((m, path), "unknown")
            matrix = POLICY_MATRIX.get(policy, {
                "manager": "?", "safety_officer": "?", "engineer": "?",
                "foreman": "?", "miner": "?", "anon": "?"
            })
            route_entries.append((path, m, policy, matrix, clean_summary))

    # Sort routes by path, then method
    route_entries.sort(key=lambda x: (x[0], x[1]))

    lines = [
        "| Method | Path | Policy | Manager | Safety Officer | Engineer | Foreman | Miner | Anon | Summary |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    for path, m, policy, matrix, summary in route_entries:
        lines.append(
            f"| `{m}` | `{path}` | `{policy}` | "
            f"{matrix['manager']} | {matrix['safety_officer']} | {matrix['engineer']} | "
            f"{matrix['foreman']} | {matrix['miner']} | {matrix['anon']} | {summary} |"
        )

    return "\n".join(lines)


if __name__ == "__main__":
    table = generate_markdown_table()
    print(table)
