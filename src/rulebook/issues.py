"""Flow 3: `/rulebook file N` turns findings from the review comment into GitHub issues.

No model is involved. The findings come from the hidden data block in the review comment,
and only from the comment this tool wrote (marker AND bot author), so nobody can feed in
findings of their own by pasting a data block. Everything that came from the pull request
or a model (paths, explanations, fixes) is escaped again before it goes into an issue.

An issue carries a hidden key (pull request + policy + file + line), so filing the same
finding twice finds the first issue instead of opening a second one.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Union
from urllib.parse import quote

from .render import code_block, code_span, escape, escape_rich

LABEL = "rulebook"
LABEL_COLOR = "6f42c1"
LABEL_DESCRIPTION = "Opened by Rulebook Agents from a pull request review"
KEY_PREFIX = "<!-- rulebook-agents:issue "
MAX_PER_COMMAND = 25
USAGE = ("Usage: `/rulebook file 1` (one finding), `/rulebook file 1 3` (several) or "
         "`/rulebook file all`. The numbers are the ones in the review comment.")

_COMMAND = re.compile(r"^\s*/rulebook\b(.*)$", re.IGNORECASE)
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


class CommandError(ValueError):
    """The comment starts with /rulebook but is not a command we understand."""


Selection = Union[str, list[int]]          # "all" or finding numbers


def parse_command(body: str) -> Optional[Selection]:
    """The selection in a `/rulebook file ...` comment. None if the comment is not addressed
    to us; CommandError if it is, but cannot be understood. Only the first line counts."""
    first = (body or "").strip().splitlines()[0] if (body or "").strip() else ""
    m = _COMMAND.match(first)
    if not m:
        return None
    words = m.group(1).replace(",", " ").split()
    if not words or words[0].lower() != "file":
        raise CommandError("unknown command")
    args = [w.lower() for w in words[1:]]
    if args == ["all"]:
        return "all"
    if not args or not all(a.lstrip("#").isdigit() for a in args):
        raise CommandError("expected finding numbers or `all`")
    numbers = sorted({int(a.lstrip("#")) for a in args})
    if not numbers or numbers[0] < 1:
        raise CommandError("finding numbers start at 1")
    return numbers


# --------------------------------------------------------------------------- findings

@dataclass(frozen=True)
class FiledFinding:
    n: int
    path: str
    line: int
    end_line: int
    policy_id: str
    policy_title: str
    severity: str
    quote: str
    explanation: str
    fix_code: str = ""
    fix_note: str = ""


def findings_from(data: dict[str, Any]) -> list[FiledFinding]:
    """The findings in a data block, with their types checked (anything malformed is skipped)."""
    out: list[FiledFinding] = []
    for item in data.get("findings") or []:
        if not isinstance(item, dict):
            continue
        try:
            n, line = int(item["n"]), int(item["line"])
            end = int(item.get("end_line") or line)
            text = {k: str(item.get(k) or "") for k in ("path", "policy_id", "policy_title", "severity",
                                                        "quote", "explanation", "fix_code", "fix_note")}
        except (KeyError, TypeError, ValueError):
            continue
        if n < 1 or line < 1 or not text["path"] or not text["policy_id"]:
            continue
        out.append(FiledFinding(n=n, line=line, end_line=max(end, line), **text))
    return out


def issue_key(pr: int, f: FiledFinding) -> str:
    raw = f"{pr}|{f.policy_id}|{f.path}|{f.line}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def key_marker(key: str) -> str:
    return f"{KEY_PREFIX}{key} -->"


def issue_title(f: FiledFinding) -> str:
    title = f"{f.policy_title or f.policy_id}: {f.path} line {f.line}"
    return " ".join(title.split())[:200]


def issue_body(f: FiledFinding, *, repo: str, pr: int, commit: str,
               server: str = "https://github.com") -> str:
    lines = f"line {f.line}" + (f"–{f.end_line}" if f.end_line != f.line else "")
    where = f"{code_span(f.path)} {lines}"
    if _SHA.match(commit or ""):
        anchor = f"#L{f.line}" + (f"-L{f.end_line}" if f.end_line != f.line else "")
        url = f"{server.rstrip('/')}/{repo}/blob/{commit}/{quote(f.path, safe='/')}{anchor}"
        where += f" ([view at {code_span(commit[:7])}]({url}))"
    out = [key_marker(issue_key(pr, f)),
           f"**Policy:** {escape(f.policy_title or f.policy_id)} · {escape(f.severity)}",
           f"**Where:** {where}",
           f"**Found in:** #{int(pr)}", ""]
    if f.quote:
        out += [f"> {escape(f.quote)}", ""]
    out += [escape_rich(f.explanation)]
    if f.fix_code or f.fix_note:
        out += ["", "**Suggested fix**"]
        if f.fix_note:
            out += ["", escape_rich(f.fix_note)]
        if f.fix_code:
            out += ["", code_block(f.fix_code, f.path)]
    out += ["", f"<sub>Filed by Rulebook Agents with `/rulebook file` on #{int(pr)}.</sub>", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------- filing

@dataclass
class FileResult:
    opened: list[tuple[int, int]] = field(default_factory=list)     # (finding, issue)
    existing: list[tuple[int, int]] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)                # numbers not in the review
    capped: int = 0                                                 # over MAX_PER_COMMAND


def existing_issues(issues: list[dict], authors: Iterable[str]) -> dict[str, int]:
    """key -> issue number, for issues this tool opened. Only issues by the given authors (the
    bot, or the person running `rulebook file-issues`) count, so nobody can block a finding
    from being filed by writing an issue with its key."""
    authors = set(authors)
    found: dict[str, int] = {}
    for issue in issues:
        if (issue.get("user") or {}).get("login") not in authors or "pull_request" in issue:
            continue
        m = re.search(re.escape(KEY_PREFIX) + r"([0-9a-f]{16}) -->", issue.get("body") or "")
        if m:
            found.setdefault(m.group(1), int(issue["number"]))
    return found


def file_findings(gh: Any, *, pr: int, data: dict[str, Any], selection: Selection,
                  issue_authors: Iterable[str], server: str = "https://github.com") -> FileResult:
    """Open an issue for each selected finding that has none yet."""
    findings = {f.n: f for f in findings_from(data)}
    result = FileResult()
    wanted = sorted(findings) if selection == "all" else list(selection)
    result.missing = [n for n in wanted if n not in findings]
    wanted = [n for n in wanted if n in findings]
    if not wanted:
        return result
    known = existing_issues(gh.issues(label=LABEL), issue_authors)
    gh.ensure_label(LABEL, LABEL_COLOR, LABEL_DESCRIPTION)
    commit = str(data.get("commit") or "")
    for n in wanted:
        f = findings[n]
        key = issue_key(pr, f)
        if key in known:
            result.existing.append((n, known[key]))
            continue
        if len(result.opened) >= MAX_PER_COMMAND:
            result.capped += 1
            continue
        issue = gh.create_issue(issue_title(f), issue_body(f, repo=gh.repository, pr=pr, commit=commit,
                                                          server=server), [LABEL])
        known[key] = int(issue["number"])
        result.opened.append((n, int(issue["number"])))
    return result


def _list(pairs: list[tuple[int, int]]) -> str:
    return ", ".join(f"{n} → #{i}" for n, i in pairs)


def reply_markdown(result: FileResult, total: int) -> str:
    """The reply on the pull request. Built only from numbers, never from the command text."""
    out = []
    if result.opened:
        out.append(f"Opened {len(result.opened)} issue{'s' if len(result.opened) != 1 else ''} "
                   f"(finding → issue): {_list(result.opened)}.")
    if result.existing:
        out.append(f"Already filed: {_list(result.existing)}.")
    if result.capped:
        out.append(f"{result.capped} more not filed: at most {MAX_PER_COMMAND} issues per command. "
                   "Run the command again for the rest.")
    if result.missing:
        nums = ", ".join(str(n) for n in result.missing)
        out.append(f"No finding {nums} in the latest review"
                   + (f" (it has {total})." if total else " (it found no problems)."))
    return "\n\n".join(out) or "Nothing to file."
