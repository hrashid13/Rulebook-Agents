import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ScriptedModel, make_repo, needs_semgrep
from rulebook import __version__
from rulebook.cli import main
from rulebook.llm import LLMClient

ROOT = Path(__file__).resolve().parent.parent


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_policies_lists_the_example(capsys):
    assert main(["policies", str(ROOT / "examples" / "policies.md")]) == 0
    out = capsys.readouterr().out
    assert "4 policies" in out and "no-thread-abort" in out and "csharp" in out


def test_policies_reports_errors(tmp_path, capsys):
    bad = tmp_path / "policies.md"
    bad.write_text("## A\ncategory: speed\n\nText.\n", encoding="utf-8")
    assert main(["policies", str(bad)]) == 1
    assert "line 2" in capsys.readouterr().err


@pytest.fixture
def scripted_from_env(monkeypatch, tmp_path):
    """`rulebook review` builds its model client from the environment; give it the scripted model."""
    real = LLMClient.from_env

    def fake(**kw):
        llm = real(**kw)
        llm._backend = ScriptedModel()
        return llm
    monkeypatch.setattr(LLMClient, "from_env", staticmethod(fake))
    monkeypatch.setenv("RULEBOOK_LLM_MODE", "live")


@needs_semgrep
def test_review_command(tmp_path, capsys, scripted_from_env):
    repo = make_repo(tmp_path, "python")
    out = tmp_path / "report.md"
    assert main(["review", "--repo", str(repo), "--base", "main", "--output", str(out)]) == 0
    assert "2 problem(s), 1 dismissed" in capsys.readouterr().out
    assert "**2 problems** in this change" in out.read_text()
    # --fail-on error: there is an error-severity problem, so exit 1
    assert main(["review", "--repo", str(repo), "--base", "main", "--fail-on", "error",
                 "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert len(data["findings"]) == 2 and data["dismissed"][0]["alert"]["path"] == "tests/test_users.py"


def test_review_setup_errors(tmp_path, capsys):
    assert main(["review", "--repo", str(tmp_path)]) == 2      # no policies.md
    assert "not found" in capsys.readouterr().err


@needs_semgrep
def test_review_without_api_key_stops(tmp_path, capsys, monkeypatch):
    repo = make_repo(tmp_path, "csharp")
    for var in ("ANTHROPIC_API_KEY", "RULEBOOK_API_KEY", "RULEBOOK_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RULEBOOK_LLM_MODE", "live")
    assert main(["review", "--repo", str(repo), "--base", "main"]) == 3
    assert "no API key" in capsys.readouterr().err


def test_runs_as_a_module():
    out = subprocess.run([sys.executable, "-m", "rulebook", "--version"],
                         capture_output=True, text=True, check=True)
    assert __version__ in out.stdout


def test_readme_workflow_matches_the_example():
    """The README shows the whole workflow inline; it must not drift from examples/rulebook.yml."""
    example = (ROOT / "examples" / "rulebook.yml").read_text()
    body = example[example.index("name: Rulebook"):].rstrip()
    indented = "\n".join(("   " + line) if line else "" for line in body.splitlines())
    assert indented in (ROOT / "README.md").read_text()
