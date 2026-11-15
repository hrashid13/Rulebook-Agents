"""Build a small throwaway repo to try `rulebook review` on.

    python examples/make_demo_repo.py ../rulebook-demo
    rulebook review --repo ../rulebook-demo --base main

The repo gets the test fixtures' policies and rules in .rulebook/, a `main` branch with the
"before" code (Python and TypeScript), and a `feature` branch that adds problems on purpose:
  * app/users.py      SQL built by concatenating user input, and a print() call
  * tests/test_users.py  a print() in a test (should be dismissed as noise)
  * src/orders.ts     SQL in a template literal, and eval()
  * app/users.py line 13 breaks the SQL policy too, but on an unchanged line (not reported)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
LANGS = ("python", "typescript")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=Rulebook Demo", "-c", "user.email=demo@example.com",
                    "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *args],
                   cwd=repo, check=True, capture_output=True, text=True)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    repo = Path(argv[0]).resolve()
    if repo.exists() and any(repo.iterdir()):
        print(f"{repo} already exists and is not empty; pick a new folder (or delete it).")
        return 1
    repo.mkdir(parents=True, exist_ok=True)

    rulebook = repo / ".rulebook" / "rules"
    rulebook.mkdir(parents=True)
    shutil.copy(FIXTURES / "policies.md", repo / ".rulebook" / "policies.md")
    for lang in LANGS:
        for rule in (FIXTURES / lang / "rules").glob("*.yml"):
            shutil.copy(rule, rulebook / f"{rule.stem}-{lang}.yml")
        shutil.copytree(FIXTURES / lang / "before", repo, dirs_exist_ok=True)
    (repo / ".gitignore").write_text(".env\n.rulebook-cache/\n", encoding="utf-8")

    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Base: the code before the change")
    git(repo, "checkout", "-q", "-b", "feature")
    for lang in LANGS:
        shutil.copytree(FIXTURES / lang / "after", repo, dirs_exist_ok=True)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Feature: a change with some policy problems")

    print(f"Demo repo ready: {repo}  (branch 'feature', compared with 'main')")
    print(f"Now run:  rulebook review --repo \"{repo}\" --base main")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
