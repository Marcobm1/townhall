# AGENTS.md — working on Town Hall

Town Hall is an open-source multi-agent deliberation library and CLI designed to
run standalone and mount inside [Huo / Baton](https://github.com/Marcobm1/baton).
Read `README.md` and `docs/architecture.md` first.

- Talk to the owner (Marco) in Spanish; code, comments, docs and commits in English.
- Marco builds this to learn: explain the *why* of each step in the conversation
  (alternatives, trade-offs, new concepts with an example, what each part of a
  command does, the cause of any failure), and record important decisions in `docs/`.
- Keep the core package (`townhall/`) standard-library only unless Marco
  approves an optional extra, and pin all dependencies to exact versions in
  `pyproject.toml`.
- **Read-only by design:** Town Hall participants review, debate, and synthesize
  — they must NEVER mutate the target repository. Any CLI adapter that can edit
  files (such as `agy`) must run inside a disposable git worktree or strict
  read-only boundary.
- **No secrets in the repo:** never read, store, or forward provider credentials;
  strip host secrets from subprocess environments.
- **GitHub Actions:** `permissions: {}` at top level, minimal per-job permissions,
  only GitHub-made `actions/*` pinned to a full commit SHA with the version in a comment.
- Run linter and tests before finishing: `pip install -e ".[dev]" && ruff check townhall tests && pytest -q`.

## Project status (Baton / Huo)

This repository is tracked with [Baton / Huo](https://github.com/Marcobm1/baton).
`STATUS.md` is the single source of truth for where the project stands.

- **Before you start:** read `STATUS.md`. Its `next` field and its "Next" list
  say what comes next. If the owner asked for something else, do that.
- **When you finish a work session that changed anything** (code, docs, data,
  decisions), update `STATUS.md` in the same commit or pull request:
  - front matter: `updated` = today (YYYY-MM-DD), `updated_by` = your tool
    (`claude-code`, `codex`, `gemini`, `antigravity`, `human`, ...), `next` = the
    next concrete step. Change `status` or `priority` only when the owner decides it;
  - tick finished tasks (`- [x]`) and add new ones (`- [ ]`) under "Next";
    make the first unchecked agent task self-contained with title, indented
    description, verification, and `Recommended: <agent>, <model>, <effort>`;
  - update "Blockers" and "Decisions" if they changed;
  - add one line at the top of "Log": `- YYYY-MM-DD (tool): what changed`.
- Never put secrets, tokens, passwords or personal data in `STATUS.md`.
