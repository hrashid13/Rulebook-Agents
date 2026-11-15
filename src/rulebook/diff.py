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


def changed_lines(repo: str | Path, base: str, *, merge_base: bool = True) -> dict[str, set[int]]:
    """merge_base=False diffs against `base` itself: right for a pull-request checkout, where
    HEAD is GitHub's merge of the PR into the base commit."""
    repo = Path(repo)
    start = _git(repo, "merge-base", base, "HEAD").strip() if merge_base else base
    text = _git(repo, "diff", "--no-color", "--no-ext-diff", "--unified=0", "--find-renames",
                "--src-prefix=a/", "--dst-prefix=b/", start)
    return parse_unified_diff(text)


def has_commit(repo: str | Path, sha: str) -> bool:
    try:
        _git(Path(repo), "cat-file", "-e", f"{sha}^{{commit}}")
        return True
    except DiffError:
        return False


def ensure_commit(repo: str | Path, sha: str) -> None:
    """A shallow checkout (the default fetch-depth: 1) may not have the base commit."""
    if not has_commit(repo, sha):
        _git(Path(repo), "fetch", "--no-tags", "--depth=1", "origin", sha)


def export_folder(repo: str | Path, rev: str, folder: str, dest: str | Path) -> bool:
    """Copy `folder` as it is at commit `rev` into `dest`, without touching the working tree.
    Returns False if the folder does not exist at that commit. Symlinks are not exported."""
    repo, dest = Path(repo), Path(dest)
    listing = _git(repo, "ls-tree", "-r", "-z", rev, "--", folder)
    entries = [e for e in listing.split("\0") if e]
    if not entries:
        return False
    for entry in entries:
        meta, path = entry.split("\t", 1)
        mode, kind = meta.split()[:2]
        if kind != "blob" or mode == "120000":        # skip submodules and symlinks
            continue
        target = (dest / path).resolve()
        target.relative_to(dest.resolve())          # a crafted path must not escape dest
        target.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=repo, capture_output=True,
                              stdin=subprocess.DEVNULL, check=False)
        if proc.returncode != 0:
            raise DiffError(f"git show {rev}:{path} failed: {proc.stderr.decode(errors='replace')[:200]}")
        target.write_bytes(proc.stdout)
    return True
