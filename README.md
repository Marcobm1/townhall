# Town Hall

An open-source multi-agent deliberation library and CLI where your AI models
debate a question, review a project, choose a 4th guest model by consensus, and
deliver **three artefacts every time**:

1. **Conclusion & action plan** — what the models agreed on (and any open disagreements).
2. **Executive summary** — who voted for which 4th model, key agreements, and trade-offs.
3. **Full transcript** — every round and message saved in SQLite and exportable to Markdown.

Town Hall is built as a **zero-dependency Python library** (standard library
only) so anyone can run it standalone via CLI/Python, and hosts like
[Huo / Baton](https://github.com/Marcobm1/baton) can mount it directly into a
web UI (`/townhall`) with live SSE streaming, Telegram alerts, and task bridges.

## Two deliberation modes

- **`council` (structured debate & project review):**
  1. **Phase 0 — Consensus guest pick:** your core models (e.g. Gemini, Codex/GPT, Claude)
     vote on a 4th model from an OpenCode/guest pool (`majority -> Borda count -> deterministic tie-break`).
  2. **Phase 1 — Blind independent analysis (parallel):** all 4 inspect the topic/folder
     without seeing each other's answers (prevents anchoring / sycophancy).
  3. **Phase 2 — Cross-critique (1–2 rounds, parallel):** each model reads the other 3
     positions and states what it accepts, challenges, or changes.
  4. **Phase 3 — Synthesis:** the chosen moderator produces the summary, conclusion,
     open disagreements, and prioritized action plan.
- **`open_floor` (time-boxed open discussion):**
  - Participants converse turn-by-turn until a time budget (`max_duration_seconds`)
    or turn cap (`max_turns`) is reached, then a moderator closes with a summary
    and conclusion so no session ever runs away or burns quotas indefinitely.

## Quick start (Phase 1 demo)

```bash
pip install -e ".[dev]"

# Run a local offline demo (4 mock participants: Gemini, Codex, Claude + consensus OpenCode guest)
townhall demo "Review the project homepage and decide what is missing"

# Inspect saved sessions
townhall list
townhall show <session-id>
townhall export <session-id> -o report.md
```

## Development

```bash
pip install -e ".[dev]"
ruff check townhall tests
pytest -q
```

See [docs/architecture.md](docs/architecture.md) for the design, security model
(read-only + disposable worktrees), and roadmap (Phases 1–4).
