"""Shared test helpers: fixture git repos and a scripted model (no real API calls)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from rulebook.llm import LLMClient, RawCompletion
from rulebook.semgrep import find_semgrep

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LANGUAGES = ("python", "typescript", "java", "go", "csharp")

# Locally, tests that need Semgrep are skipped when it is missing; CI sets
# RULEBOOK_REQUIRE_SEMGREP=1 so they can never be skipped silently there.
needs_semgrep = pytest.mark.skipif(
    find_semgrep() is None and not os.environ.get("RULEBOOK_REQUIRE_SEMGREP"),
    reason="Semgrep is not installed")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout


def make_repo(tmp_path: Path, lang: str) -> Path:
    """A repo whose main branch has the fixture's `before` files plus .rulebook/, and a
    feature branch that changes them to the `after` files (committed)."""
    repo = tmp_path / f"repo-{lang}"
    shutil.copytree(FIXTURES / lang / "before", repo)
    (repo / ".rulebook").mkdir()
    shutil.copy(FIXTURES / "policies.md", repo / ".rulebook" / "policies.md")
    shutil.copytree(FIXTURES / lang / "rules", repo / ".rulebook" / "rules")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "feature")
    shutil.copytree(FIXTURES / lang / "after", repo, dirs_exist_ok=True)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")
    return repo


class ScriptedModel:
    """Answers by reading the prompt, like a model at temperature 0.

    Agent 1: NOISE for code under tests/ or a query built from the constant `table`;
             otherwise REAL. Agent 2: quotes the policy's first sentence.
    `script` can override answers: {"agent1": [...texts], "agent2": [...texts]} are used
    first, in order."""

    def __init__(self, script: dict[str, list[str]] | None = None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[dict] = []

    def complete(self, *, model, system, messages, temperature, max_tokens) -> RawCompletion:
        prompt = messages[0]["content"]
        agent = "agent1" if system.startswith("You triage") else "agent2"
        self.calls.append({"agent": agent, "system": system, "messages": messages})
        if self.script.get(agent):
            return RawCompletion(self.script[agent].pop(0), "end_turn", 100, 20)
        if agent == "agent1":
            flagged = "\n".join(l for l in prompt.splitlines() if l.startswith(">>"))
            noise = "File: tests/" in prompt or "+ table +" in flagged
            return RawCompletion(json.dumps({"decision": "NOISE" if noise else "REAL",
                                             "reason": "test code or a constant" if noise
                                             else "untrusted input reaches the call"}), "end_turn", 100, 20)
        policy = re.search(r'Policy "[^"]+":\n(.+?)\n\n', prompt, re.S).group(1)
        sentence = re.match(r"(.+?[.!?])(\s|$)", " ".join(policy.split())).group(1)
        return RawCompletion(json.dumps({"explanation": "This line breaks the policy.",
                                         "quote": sentence, "fix_code": "fixed(code)",
                                         "fix_note": "Use the safe API."}),
                             "end_turn", 120, 40)


def scripted_llm(tmp_path: Path, script: dict | None = None, mode: str = "live") -> LLMClient:
    llm = LLMClient(mode=mode, cache_dir=tmp_path / "cache", backend=ScriptedModel(script))
    return llm
