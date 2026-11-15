# Rulebook Agents

Write your team's coding rules in plain English. Rulebook Agents turns each one into a tested [Semgrep](https://semgrep.dev) rule, checks every pull request against them, and explains each real problem it finds, quoting the rule it breaks.

- **Rules:** an agent drafts a Semgrep rule and a test file for each policy and opens a pull request with them. Nothing is enforced until a person merges it.
- **Review:** on every pull request, Semgrep scans the changed lines, one agent separates real problems from noise, and another explains each real problem and quotes your policy word for word.
- **Report:** one comment on the PR, updated on every push. A maintainer can turn any finding into a GitHub issue with `/rulebook file 1`.

Works with any language Semgrep supports, and any model with an Anthropic or OpenAI-compatible API.

> **Status: early development.** Reviews work, locally and as a GitHub Action. Rules are written by hand until rule generation (M3) lands. The design is in [docs/DESIGN.md](docs/DESIGN.md).

## Use it on your pull requests (GitHub Action)

1. **Add your rules** on your default branch: `.rulebook/policies.md` (your policies in plain English; see [examples/policies.md](examples/policies.md)) and `.rulebook/rules/` (Semgrep rules, each with `metadata.policy` set to the policy's id; see [tests/fixtures](tests/fixtures) for examples in five languages). The Action always reads these **from the base branch**, so a pull request cannot change the rules it is checked against.
2. **Add your API key** as a repository secret named `ANTHROPIC_API_KEY` (Settings > Secrets and variables > Actions).
3. **Add the workflow:** copy [examples/rulebook.yml](examples/rulebook.yml) to `.github/workflows/rulebook.yml`.

Every pull request then gets one comment with the problems found, updated on each push, plus inline annotations on the changed lines. The check passes unless you set `fail-on`.

| Input | Default | What it does |
|---|---|---|
| `api-key` | | Model API key (pass a secret) |
| `provider` | `anthropic` | Or `openai` for any OpenAI-compatible API |
| `model` | `claude-haiku-5-5` | Model name (required for `openai`) |
| `base-url` | | API URL for `openai`, e.g. `https://api.openai.com/v1` |
| `fail-on` | `none` | `error` or `any`: when the check should fail |
| `comment` | `true` | `false`: report only in the job summary |
| `max-calls` | `200` | Cap on model calls per run |

Outputs: `problems`, `dismissed`, `status` (`clean`, `problems`, `skipped`, `failed`).

Good to know:
- It runs on Linux runners (`ubuntu-latest`).
- Pull requests from forks are skipped: GitHub gives them no secrets.
- If the model or Semgrep fails, the check fails and the comment says nothing was reviewed. An outage never looks like a clean review.
- Nothing from the pull request or the model is printed to the job log, and only the bot's own comment is ever edited.

## Use it locally

You need Python 3.11+, git, and [Semgrep](https://semgrep.dev) installed in its own environment:

```
uv tool install semgrep          # or: pipx install semgrep
pip install -e ".[dev]"
```

**Check your policies file** (clear errors with line numbers for anything it can't read):

```
rulebook policies .rulebook/policies.md
```

**Review your branch** against the rules in `.rulebook/rules/`:

```
export ANTHROPIC_API_KEY=...            # PowerShell: $env:ANTHROPIC_API_KEY="..."
rulebook review --base main
```

It looks at the lines your branch changed (including uncommitted edits to tracked files), runs your Semgrep rules on them, has one agent separate real problems from noise and another explain each real problem with a word-for-word quote of the policy, and prints a Markdown report. Options:

| Option | What it does |
|---|---|
| `--base REF` | Branch to compare with (default: origin's default branch, then `main`/`master`) |
| `--output FILE` | Write the report to a file |
| `--format json` | Machine-readable report |
| `--fail-on error\|any` | Exit 1 when problems reach that level (default `none`) |

Answers are cached in `.rulebook-cache/`, so running the review again on unchanged code costs nothing. Rules are written by hand for now; generating them from `policies.md` is milestone M3. See [examples/policies.md](examples/policies.md) for the policy format and [tests/fixtures](tests/fixtures) for example rules in Python, TypeScript, Java, Go and C#.

**Model settings** (environment variables, all optional except the key):

| Variable | Default |
|---|---|
| `ANTHROPIC_API_KEY` or `RULEBOOK_API_KEY` | (required) |
| `RULEBOOK_PROVIDER` | `anthropic`; or `openai` for any OpenAI-compatible API (OpenAI, OpenRouter, Ollama) |
| `RULEBOOK_MODEL` | `claude-haiku-5-5` for Anthropic |
| `RULEBOOK_BASE_URL` | needed for `openai`, e.g. `http://localhost:11434/v1` |
| `RULEBOOK_MAX_CALLS` | `200` model calls per run |
| `RULEBOOK_PRICE_IN_PER_MTOK` / `_OUT_` | `0` (set them to see cost in the report) |

## Roadmap

| Milestone | What |
|---|---|
| M0 | Package, CLI, `policies.md` parser, CI |
| M1 | `rulebook review` on a local branch, in several languages |
| M2 | The review flow as a GitHub Action: PR comment and check |
| M3 | `rulebook rules`: policies to tested Semgrep rules, opened as a PR |
| M4 | `/rulebook file N`: findings to GitHub issues |
| M5 | Published image, `v1` release, GitHub Marketplace listing |

## Credits

Rulebook Agents grew out of **Docker Locker**, a final project for ISM 6564 (Text Analytics) at the University of South Florida by Hesham Rashid and Zachary Zambrana. The triage agent, the rule synthesizer, the PR diff handling and the evaluation approach started from Zachary's code in that project; the policy-citation agent, model client, guardrails and red-team suite started from Hesham's. Source: `ISM-6564-Final-Project-Docker-Locker` at commit `c49d12c`.

## License

[MIT](LICENSE)
