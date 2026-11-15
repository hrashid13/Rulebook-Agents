"""Which lines did this change add or modify?

The review compares the working tree with the point where the branch left the base
(`git merge-base BASE HEAD`). In a GitHub Action the working tree is the PR's head commit;
locally it also includes uncommitted edits to tracked files, so you can review before you
commit. New files must be added (`git add`) to be seen.

The unified-diff parser started from Zachary Zambrana's get_pr_diff in Docker Locker.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class DiffError(RuntimeError):
    pass


def _unquote(path: str) -> str:
    """git quotes paths with unusual characters: "b/caf\\303\\251.py"."""
    path = path.strip()
    if len(path) >= 2 and path[0] == path[-1] == '"':
        raw = path[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
        path = raw.encode("latin-1").decode("utf-8", "replace")
    return path


def parse_unified_diff(text: str) -> dict[str, set[int]]:
    """{repo-relative path: line numbers added or changed in the new version}.
    Deleted files are left out: there is nothing left to scan."""
    out: dict[str, set[int]] = {}
    current: str | None = None
    new_line = 0
    for line in text.splitlines():
        if line.startswith("+++ "):
            target = _unquote(line[4:])
            if target == "/dev/null":
                current = None
                continue
            current = target[2:] if target.startswith("b/") else target
            out.setdefault(current, set())
        elif line.startswith("@@ "):
            m = _HUNK.match(line)
            new_line = int(m.group(1)) if m else 0
        elif current is not None and new_line:
            if line.startswith("+"):
                out[current].add(new_line)
                new_line += 1
            elif line.startswith(" "):
                new_line += 1
            # "-" lines and "\ No newline at end of file" do not move the new-file counter
    return {path: lines for path, lines in out.items() if lines}


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-c", "core.quotePath=true", *args], cwd=repo,
                              capture_output=True, text=True, encoding="utf-8",
                              stdin=subprocess.DEVNULL, check=False)
    except FileNotFoundError as exc:
        raise DiffError("git is not installed or not on PATH") from exc
    if proc.returncode != 0:
        raise DiffError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def default_base(repo: str | Path) -> str:
    """origin's default branch if known, else main or master."""
    for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
        try:
            _git(Path(repo), "rev-parse", "--verify", "--quiet", ref)
            return ref
        except DiffError:
            continue
    raise DiffError("could not find a base branch; pass --base (for example --base origin/main)")


def changed_lines(repo: str | Path, base: str) -> dict[str, set[int]]:
    repo = Path(repo)
    merge_base = _git(repo, "merge-base", base, "HEAD").strip()
    text = _git(repo, "diff", "--no-color", "--no-ext-diff", "--unified=0", "--find-renames",
                "--src-prefix=a/", "--dst-prefix=b/", merge_base)
    return parse_unified_diff(text)
