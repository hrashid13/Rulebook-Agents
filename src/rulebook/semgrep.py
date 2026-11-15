"""Running Semgrep. There is no fallback matcher: without Semgrep, the review refuses to
run and says how to install it.

Semgrep belongs in its own environment (`uv tool install semgrep` or `pipx install
semgrep`), not next to this package: it pins dependency versions that conflict with others.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .models import Alert

INSTALL_HINT = ("Semgrep is not installed (or not on PATH). Install it in its own environment:\n"
                "    uv tool install semgrep      (or: pipx install semgrep)\n"
                "then open a new terminal and check `semgrep --version`.")
LOGIN_PLACEHOLDER = "requires login"
MAX_SNIPPET_LINES = 8
_SEVERITY = {"ERROR": "error", "WARNING": "warning", "INFO": "info",
             "CRITICAL": "error", "HIGH": "error", "MEDIUM": "warning", "LOW": "info"}


class SemgrepNotInstalled(RuntimeError):
    def __init__(self) -> None:
        super().__init__(INSTALL_HINT)


class SemgrepError(RuntimeError):
    """Semgrep ran but produced no usable output."""


def find_semgrep() -> Optional[str]:
    found = shutil.which("semgrep")
    if found:
        return found
    exe = "semgrep.exe" if os.name == "nt" else "semgrep"
    candidate = Path.home() / ".local" / "bin" / exe   # where uv tool / pipx put it
    return str(candidate) if candidate.exists() else None


def run_semgrep(config: str | Path, targets: Sequence[str], *, cwd: str | Path,
                timeout_s: int = 300) -> dict[str, Any]:
    """`semgrep --json` on exactly these targets. Files named explicitly are scanned even in
    folders Semgrep skips by default (tests/, vendor/): the diff decides what is in scope."""
    exe = find_semgrep()
    if not exe:
        raise SemgrepNotInstalled()
    cmd = [exe, "--config", str(config), "--json", "--metrics=off", "--disable-version-check",
           "--quiet", "--no-git-ignore", *targets]
    try:
        # stdin=DEVNULL: Semgrep must never inherit our stdin (it can hang on Windows).
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              stdin=subprocess.DEVNULL, check=False, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise SemgrepError(f"semgrep timed out after {timeout_s}s") from exc
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise SemgrepError(f"semgrep exited {proc.returncode} without JSON output: {tail}") from exc


def read_snippet(repo: Path, path: str, start: int, end: int, fallback: str = "") -> str:
    """The matched code, read from the file (Semgrep without a login returns the text
    "requires login" instead of the code)."""
    end = min(max(end, start), start + MAX_SNIPPET_LINES - 1)
    try:
        lines = (repo / path).read_text(encoding="utf-8").splitlines()
        snippet = "\n".join(lines[start - 1:end]).rstrip()
        if snippet.strip():
            return snippet
    except (OSError, UnicodeDecodeError):
        pass
    return "" if fallback.strip() == LOGIN_PLACEHOLDER else fallback.rstrip()


def error_messages(data: dict[str, Any]) -> list[str]:
    out = []
    for e in data.get("errors") or []:
        msg = e.get("message") or e.get("long_msg") or e.get("short_msg") or str(e)
        out.append(" ".join(str(msg).split())[:300])
    return out


def policy_for(rule_id: str, metadata: dict[str, Any], policy_ids: Iterable[str]) -> Optional[str]:
    """Which policy a rule enforces: its `metadata.policy`, or failing that a rule id that
    is the policy id or starts with it ("parameterized-sql-only-python")."""
    ids = set(policy_ids)
    declared = str((metadata or {}).get("policy") or "")
    if declared:
        return declared if declared in ids else None
    for pid in sorted(ids, key=len, reverse=True):
        if rule_id == pid or rule_id.startswith(pid + "-"):
            return pid
    return None


def scan(repo: str | Path, rules: str | Path, changed: dict[str, set[int]],
         policy_ids: Iterable[str]) -> tuple[list[Alert], list[str]]:
    """Alerts on changed lines only, each tied to a known policy. Returns (alerts, problems):
    problems are things a person should fix (a rule that fails to parse, a rule with no
    policy), reported rather than silently dropped."""
    repo = Path(repo)
    targets = sorted(p for p in changed if (repo / p).is_file())
    if not targets:
        return [], []
    data = run_semgrep(Path(rules).resolve(), targets, cwd=repo)
    problems = [f"Semgrep: {m}" for m in error_messages(data)]
    policy_ids = list(policy_ids)
    alerts: list[Alert] = []
    orphan_rules: set[str] = set()
    seen: set[tuple] = set()
    for r in data.get("results") or []:
        path = str(r.get("path", "")).replace("\\", "/").removeprefix("./")
        start = int((r.get("start") or {}).get("line") or 1)
        end = int((r.get("end") or {}).get("line") or start)
        if not changed.get(path, set()) & set(range(start, end + 1)):
            continue   # the match is on lines this change did not touch
        rule_id = str(r.get("check_id", "")).rsplit(".", 1)[-1]
        extra = r.get("extra") or {}
        pid = policy_for(rule_id, extra.get("metadata") or {}, policy_ids)
        if pid is None:
            orphan_rules.add(rule_id)
            continue
        key = (path, start, rule_id)
        if key in seen:
            continue
        seen.add(key)
        alerts.append(Alert(
            path=path, start_line=start, end_line=end, rule_id=rule_id, policy_id=pid,
            message=" ".join(str(extra.get("message", "")).split()),
            severity=_SEVERITY.get(str(extra.get("severity", "WARNING")).upper(), "warning"),
            snippet=read_snippet(repo, path, start, end, str(extra.get("lines", ""))),
        ))
    for rule_id in sorted(orphan_rules):
        problems.append(f"Rule {rule_id} matched, but it does not name a policy in policies.md "
                        "(set metadata.policy to the policy's id); its matches were skipped.")
    return sorted(alerts, key=Alert.sort_key), problems
