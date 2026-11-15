import subprocess
import sys
from pathlib import Path

import pytest

from rulebook import __version__
from rulebook.cli import main

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


@pytest.mark.parametrize("command, milestone", [("review", "M1"), ("rules", "M3"), ("file-issues", "M4")])
def test_unbuilt_commands_say_so(command, milestone, capsys):
    assert main([command]) == 2
    assert milestone in capsys.readouterr().err


def test_runs_as_a_module():
    out = subprocess.run([sys.executable, "-m", "rulebook", "--version"],
                         capture_output=True, text=True, check=True)
    assert __version__ in out.stdout
