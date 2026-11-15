# Rulebook Agents

Write your team's coding rules in plain English. Rulebook Agents turns each one into a tested [Semgrep](https://semgrep.dev) rule, checks every pull request against them, and explains each real problem it finds, quoting the rule it breaks.

- **Rules:** an agent drafts a Semgrep rule and a test file for each policy and opens a pull request with them. Nothing is enforced until a person merges it.
- **Review:** on every pull request, Semgrep scans the changed lines, one agent separates real problems from noise, and another explains each real problem and quotes your policy word for word.
- **Report:** one comment on the PR, updated on every push. A maintainer can turn any finding into a GitHub issue with `/rulebook file 1`.

Works with any language Semgrep supports, and any model with an Anthropic or OpenAI-compatible API.

## How well it works

Measured with Claude Haiku (the default) on hand-labelled data in Python, TypeScript, Java, Go and C#. Details, and how to rerun it, in [eval/](eval/README.md).

| | First run | After fixing what it found |
|---|---|---|
| Real problems kept (38 alerts: 17 real, 21 noise) | 17/17 | 17/17 |
| Noise dismissed | 14/21 | 21/21 |
| Policy quoted word for word (after at most one retry) | 16/17 | 17/17 |
| Rules written that pass their own test (13 policy and language pairs) | 10/13 | 13/13 |
| Generated rules on held-out tests they never saw: precision / recall | 100% / 66% | 100% / 90% |
| Prompt-injection attacks that got a real problem dismissed | 0/7 | 0/7 |
| Live mentions, links or workflow commands in the posted output | 0 | 0 |

How to read it:
- **The first run found two real weaknesses,** and the prompts were fixed in general terms, not case by case. Agent 1 was calling a safe line a problem because a *different* line in the file broke the policy; it now judges only the flagged lines, and also sees the top of the file, where constants live. The rule writer was told to be "as precise as possible", so it filtered on variable names (a `Thread.Abort` rule that only matched variables called `thread`) and ran out of tokens; it now casts a wide net and leaves context to Agent 1, as the design intends.
- **The second column was measured on the same cases that exposed the problems,** so treat it as a best case. The set is small: 38 alerts and 13 rules.
- **Generated rules never flagged compliant code,** but they still miss some forms (the secrets rules most often), which is why every rule arrives as a pull request for a person to review.
- **Haiku resisted every injection on its own,** with the guardrails on or off, so this run doesn't show what the guardrails add; the unit tests check that they work. All model output is escaped before posting either way.

## Use it on your pull requests (GitHub Action)

Setup takes about five minutes. The Marketplace's "Use latest version" snippet is only the `uses:` line; the action also needs the triggers and permissions below, so copy the whole workflow.

1. **Add your API key.** Create one in the [Anthropic Console](https://console.anthropic.com) and add it to your repository as a secret named `ANTHROPIC_API_KEY` (Settings > Secrets and variables > Actions > New repository secret).
2. **Let Actions open pull requests:** Settings > Actions > General > Workflow permissions > tick "Allow GitHub Actions to create and approve pull requests", then Save. The rules arrive as a pull request.
3. **Add the workflow** as `.github/workflows/rulebook.yml` (the same file as [examples/rulebook.yml](examples/rulebook.yml)). If your default branch isn't `main`, change `branches: [main]`.

   ```yaml
   name: Rulebook

   on:
     pull_request:                                  # review every pull request
       types: [opened, synchronize, reopened, ready_for_review]
     push:                                          # policies changed on main: write their rules
       branches: [main]
       paths: [.rulebook/policies.md, .rulebook/config.yml]
     workflow_dispatch:                             # or run the rules job by hand (Actions tab)
     issue_comment:                                 # `/rulebook file 1` on a PR: open an issue
       types: [created]

   permissions: {}                                  # each job asks only for what it needs

   jobs:
     review:
       if: github.event_name == 'pull_request'
       runs-on: ubuntu-latest
       permissions:
         contents: read          # read the code
         pull-requests: write    # post and update the review comment
       steps:
         - uses: actions/checkout@v4
           with:
             fetch-depth: 0      # the review compares the PR with its base branch
         - uses: hrashid13/Rulebook-Agents@v1   # v1 follows the latest 1.x release
           with:
             api-key: ${{ secrets.ANTHROPIC_API_KEY }}
             # fail-on: error    # make the check fail on error-severity problems

     rules:
       if: github.event_name == 'push' || github.event_name == 'workflow_dispatch'
       runs-on: ubuntu-latest
       permissions:
         contents: write         # push the rulebook/rules branch
         pull-requests: write    # open the pull request with the new rules
       steps:
         - uses: actions/checkout@v4
           with:
             fetch-depth: 0
         - uses: hrashid13/Rulebook-Agents@v1
           with:
             api-key: ${{ secrets.ANTHROPIC_API_KEY }}

     file:
       # Anyone can comment, so this job checks out no PR code, uses no API key, and the
       # Action checks the commenter has write access before filing anything.
       if: >-
         github.event_name == 'issue_comment' && github.event.issue.pull_request &&
         startsWith(github.event.comment.body, '/rulebook')
       runs-on: ubuntu-latest
       permissions:
         contents: read          # read .rulebook/config.yml from the default branch
         issues: write           # open the issues
         pull-requests: write    # reply on the pull request
       concurrency:
         group: rulebook-file-${{ github.event.issue.number }}   # one command at a time per PR
       steps:
         - uses: actions/checkout@v4
         - uses: hrashid13/Rulebook-Agents@v1
   ```

4. **Write your policies** in `.rulebook/policies.md`: one `##` heading per rule, in plain English. For example:

   ```markdown
   ## No hardcoded secrets
   category: security
   severity: error

   API keys, passwords and tokens must never appear in source code. Load them from environment variables or a secret manager.
   ```

   More examples, and the optional fields, in [examples/policies.md](examples/policies.md).
5. **Push both files to your default branch.**

**What happens next:**
1. That push starts the **rules** job (a minute or two). It opens a pull request named "Rulebook: rules for N policies" with a tested Semgrep rule per policy and language.
2. **Review and merge that pull request.** Until you do, reviews have no rules to enforce, and the review comment says so ("Not enforced yet").
3. From then on, **every pull request gets a review comment** with the problems found, each quoting the policy it breaks, with a suggested fix.
4. To track a finding, anyone with write access comments `/rulebook file 1` on the pull request, and it becomes an issue.
5. When you edit `policies.md`, a new rules pull request updates only the rules for the policies you changed.

### How each part works

**Rules:** each push that changes `policies.md` runs the rules job. An agent writes a Semgrep rule and a test file for every policy and language, `semgrep --test` must pass, and the job opens (or updates) one pull request from the `rulebook/rules` branch. **Merging that pull request is what puts the rules into effect.** Review the rules like any code; your edits are kept, and a rule is only rewritten when its policy changes. Policies the agent can't write a rule for are listed in the PR, for you to write by hand. If you push your own commits to the `rulebook/rules` branch, the job will not overwrite them.

**Reviews:** every pull request then gets one comment with the problems found, updated on each push, plus inline annotations on the changed lines. The check passes unless you set `fail-on`. Rules and policies are always read **from the base branch**, so a pull request cannot change the rules it is checked against.

**Issues:** anyone with write access can comment `/rulebook file 1` on the pull request (a finding's number from the review comment), `/rulebook file 1 3`, or `/rulebook file all`. Each finding becomes an issue labelled `rulebook`, with the quote, the explanation, the fix and a link to the line; the bot replies with the issue numbers. Filing the same finding again points to the existing issue. Comments from anyone else get a polite refusal. To turn this off, put `issues: off` in `.rulebook/config.yml`. GitHub runs comment-triggered workflows from the default branch, so the workflow file must be merged there first.

| Input | Default | What it does |
|---|---|---|
| `api-key` | | Model API key (pass a secret) |
| `provider` | `anthropic` | Or `openai` for any OpenAI-compatible API |
| `model` | `claude-haiku-5-5` | Model name (required for `openai`) |
| `base-url` | | API URL for `openai`, e.g. `https://api.openai.com/v1` |
| `fail-on` | `none` | `error` or `any`: when the check should fail |
| `comment` | `true` | `false`: report only in the job summary |
| `max-calls` | `200` | Cap on model calls per run |

Outputs: `problems`, `dismissed`, `status`, for the rules job `rules-written` and `rules-failed`, and for the file job `issues-opened`.

Good to know:
- It runs on Linux runners (`ubuntu-latest`).
- Pull requests from forks are skipped: GitHub gives them no secrets.
- If the model or Semgrep fails, the check fails and the comment says nothing was reviewed. An outage never looks like a clean review.
- Nothing from the pull request or the model is printed to the job log, and only the bot's own comment is ever edited.

## Use it locally

You need Python 3.11+, git, and [Semgrep](https://semgrep.dev), each installed in its own environment:

```
pipx install git+https://github.com/hrashid13/Rulebook-Agents@v1
pipx install semgrep             # or: uv tool install semgrep
```

To work on Rulebook Agents itself, clone the repo and run `pip install -e ".[dev]"`, then `pytest`.

**Check your policies file** (clear errors with line numbers for anything it can't read):

```
rulebook policies .rulebook/policies.md
```

**Write rules from your policies** into `.rulebook/rules/` (review them with `git diff`, then commit):

```
rulebook rules --dry-run      # what would change, no model calls
rulebook rules                # write and test the rules
rulebook rules --only no-eval # just one policy
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

**File issues** for a pull request's findings, as `/rulebook file` does, with your own token:

```
export GITHUB_TOKEN=...                 # a token that can write issues
rulebook file-issues 1 3 --pr 7 --repository owner/name
```

Answers are cached in `.rulebook-cache/`, so running the review again on unchanged code costs nothing. See [examples/policies.md](examples/policies.md) for the policy format. You can also write rules by hand: any Semgrep rule in `.rulebook/rules/` with `metadata.policy` set to a policy's id is used, and a rule without `metadata.generated_by` is never touched by `rulebook rules`.

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

v1 is complete: policies to tested rules (opened as a PR), reviews on every pull request, and issues on command. The design and the reasoning behind it are in [docs/DESIGN.md](docs/DESIGN.md); changes are listed in [CHANGELOG.md](CHANGELOG.md).

Ideas after v1:
- a one-off scan of the whole repository, for code that existed before a rule did;
- reporting hand-edited rules that no longer pass `semgrep --test` in the review;
- a published container image and a PyPI package, if install time or demand calls for them.

## Credits

Rulebook Agents grew out of **Docker Locker**, a final project for ISM 6564 (Text Analytics) at the University of South Florida by Hesham Rashid and Zachary Zambrana. The triage agent, the rule synthesizer, the PR diff handling and the evaluation approach started from Zachary's code in that project; the policy-citation agent, model client, guardrails and red-team suite started from Hesham's. Source: `ISM-6564-Final-Project-Docker-Locker` at commit `c49d12c`.

## License

[MIT](LICENSE)
