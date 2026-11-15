"""The `rulebook` command.

    rulebook policies [PATH]   check policies.md and list what it defines
    rulebook review            review a branch's changes (milestone M1)
    rulebook rules             draft and test rules from policies.md (milestone M3)
    rulebook file-issues       open issues for findings (milestone M4)
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import __version__
from .policies import PolicyError, load_policies

DEFAULT_POLICIES = ".rulebook/policies.md"


def _not_built(milestone: str):
    def run(args: argparse.Namespace) -> int:
        print(f"`rulebook {args.command}` is not built yet (milestone {milestone}).", file=sys.stderr)
        return 2
    return run


def _policies(args: argparse.Namespace) -> int:
    try:
        policies = load_policies(args.path)
    except PolicyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"{len(policies)} polic{'y' if len(policies) == 1 else 'ies'} in {args.path}")
    for p in policies:
        langs = ", ".join(p.languages) or "repo languages"
        print(f"  {p.id:<32} {p.category or '-':<9} {p.severity:<8} {langs}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rulebook",
        description="Plain-English coding rules, turned into tested Semgrep rules and enforced on every PR.")
    parser.add_argument("--version", action="version", version=f"rulebook-agents {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("policies", help="check policies.md and list what it defines")
    p.add_argument("path", nargs="?", default=DEFAULT_POLICIES)
    p.set_defaults(func=_policies)

    for name, milestone, text in (("review", "M1", "review a branch's changes against the rules"),
                                  ("rules", "M3", "draft and test Semgrep rules from policies.md"),
                                  ("file-issues", "M4", "open GitHub issues for findings")):
        s = sub.add_parser(name, help=f"{text} (not built yet)")
        s.set_defaults(func=_not_built(milestone))
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
