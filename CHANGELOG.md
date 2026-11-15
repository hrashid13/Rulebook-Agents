# Changelog

## 1.0.0

The first release.

- **Rules:** `rulebook rules` and the rules job turn each policy in `.rulebook/policies.md` into a Semgrep rule and a test file, keep only rules that pass `semgrep --test` (up to three attempts each), and open one pull request from the `rulebook/rules` branch. A rule is only rewritten when its policy changes, and commits people push to the branch are never overwritten.
- **Reviews:** on every pull request, Semgrep scans the changed lines, Agent 1 separates real problems from noise, and Agent 2 explains each real problem with a word-for-word quote of the policy and a suggested fix. One comment, updated on each push, plus annotations and an optional failing check (`fail-on`). Policies without rules are called out instead of looking checked.
- **Issues:** `/rulebook file N` (or `all`) on a pull request opens an issue per finding, for people with write access; filing again points to the existing issue. Also `rulebook file-issues` locally.
- **Languages:** Python, TypeScript, Java, Go and C# are tested end to end; rules can be generated for 14 more (JavaScript, Ruby, Rust, Terraform and others), and hand-written rules work for anything Semgrep supports.
- **Models:** Claude Haiku by default; any Anthropic or OpenAI-compatible API (OpenAI, OpenRouter, Ollama).
- **Security:** policies and rules are read from the base branch, never the PR; nothing untrusted reaches the job log; model output is escaped before posting; fork PRs and `pull_request_target` are refused; comment commands check permissions and read no PR code.
- **Evaluation:** `eval/` with labelled triage cases in five languages, a red-team set, and held-out tests for rule synthesis. Results in `eval/results/`.
