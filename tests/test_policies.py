import re
from pathlib import Path

import pytest

from rulebook.policies import Policy, PolicyError, load_policies, parse_policies, slugify

ROOT = Path(__file__).resolve().parent.parent

BASIC = """# Our rules

Intro text before the first heading is ignored.

## No Thread.Abort
category: bug_risk
languages: C#, cs
severity: error

Do not call Thread.Abort() in managed code.
Use a CancellationToken for cooperative cancellation.

## Parameterized SQL only
category: Security
languages: py, TS java

SQL must be built with parameters or an ORM.
"""


def test_parses_policies_and_fields():
    abort, sql = parse_policies(BASIC)
    assert abort == Policy(id="no-thread-abort", title="No Thread.Abort",
                           text="Do not call Thread.Abort() in managed code.\n"
                                "Use a CancellationToken for cooperative cancellation.",
                           category="bug_risk", languages=("csharp",), severity="error")
    assert sql.id == "parameterized-sql-only"
    assert sql.category == "security"
    assert sql.languages == ("python", "typescript", "java")
    assert sql.severity == "warning"            # the default
    assert abort.line == 5 and sql.line == 13   # for error messages


def test_fields_are_optional():
    (p,) = parse_policies("## Use logging\n\nUse the logging module, not print().\n")
    assert (p.category, p.languages, p.severity) == (None, (), "warning")


def test_a_field_after_the_text_is_part_of_the_text():
    (p,) = parse_policies("## Rule\nThe rule.\ncategory: style\n")
    assert p.category is None and p.text == "The rule.\ncategory: style"


def test_headings_inside_code_blocks_are_not_policies():
    md = "## Real\nText with an example:\n```\n## not a policy\n```\n"
    (p,) = parse_policies(md)
    assert p.id == "real" and "## not a policy" in p.text


def test_slugify():
    assert slugify("No Thread.Abort()") == "no-thread-abort"
    assert slugify("  SQL -- parameters ONLY!  ") == "sql-parameters-only"


@pytest.mark.parametrize("md, message, line", [
    ("## A\ncategory: speed\n\nText.", "category must be one of", 2),
    ("## A\nseverity: fatal\n\nText.", "severity must be one of", 2),
    ("## A\nlanguages: python, cobol\n\nText.", "unknown language 'cobol'", 2),
    ("## A\ncategory: style\ncategory: style\n\nText.", "set twice", 3),
    ("## A\ncategory: style\n", "has no text", 1),
    ("## !!!\nText.", "needs letters or digits", 1),
    ("## No SQL\nOne.\n\n## no sql\nTwo.", "same id (no-sql) as the policy on line 1", 4),
])
def test_errors_point_at_the_line(md, message, line):
    with pytest.raises(PolicyError, match=re.escape(message)) as exc:
        parse_policies(md)
    assert exc.value.line == line
    assert f"line {line}" in str(exc.value)


def test_fingerprint_tracks_what_shapes_the_rule():
    (a,) = parse_policies("## Rule\ncategory: style\n\nUse logging.")
    (same,) = parse_policies("## Rule\ncategory: style\n\n  Use   logging.  ")   # whitespace only
    (text,) = parse_policies("## Rule\ncategory: style\n\nUse structured logging.")
    (cat,) = parse_policies("## Rule\ncategory: bug_risk\n\nUse logging.")
    assert a.fingerprint == same.fingerprint
    assert len({a.fingerprint, text.fingerprint, cat.fingerprint}) == 3


def test_empty_file_has_no_policies():
    assert parse_policies("# Nothing yet\n") == []


def test_missing_file(tmp_path):
    with pytest.raises(PolicyError, match="not found"):
        load_policies(tmp_path / "policies.md")


def test_example_file_parses():
    policies = load_policies(ROOT / "examples" / "policies.md")
    assert [p.id for p in policies] == ["parameterized-sql-only", "no-hardcoded-secrets",
                                        "no-thread-abort", "no-print-debugging"]
