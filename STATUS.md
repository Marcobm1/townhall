---
name: Town Hall
status: active
priority: medium
summary: Open-source multi-agent Town Hall where AI models debate, choose a 4th guest model by consensus, and produce a full transcript, summary, and conclusion.
next: "Phase 3: Standalone mini-web UI + recurring schedule runner (weekly project reviews)"
updated: 2026-11-27
updated_by: antigravity
open_source: true
tags: [tooling, ai-agents, multi-agent]
links:
  Architecture: docs/architecture.md
  Future ideas: docs/ideas.md
  Huo / Baton: https://github.com/Marcobm1/baton
---

## Done recently
- 2026-11-27: Phase 2 — Implemented read-only one-shot CLI adapters (`ClaudeCLIAdapter`, `CodexCLIAdapter`, `GeminiCLIAdapter`, `OpenCodeCLIAdapter` with deny-first `townhall-ro` rules) + stdlib `OpenAIHTTPAdapter`, `sanitize_env()` secret stripping, automatic detached `disposable_worktree()` git guard wired into `TownHallEngine`, `townhall run` & `townhall adapters` CLI commands, and cross-platform tests (`tests/test_cli_adapters.py`).
- 2026-11-27: Phase 1 — Initialized open-source repository `Marcobm1/townhall` with architecture docs (`docs/architecture.md`), zero-dependency core engine (`townhall/engine.py`, `townhall/consensus.py`, `townhall/prompts.py`, `townhall/adapter.py`, `townhall/models.py`), SQLite WAL store + Markdown exporter (`townhall/store.py`), CLI (`townhall/cli.py`), hardened CI, and unit/integration tests.

## Next
- [x] 2026-11-27 Phase 1: Architecture, core deliberation engine (`council` + `open_floor` modes), Borda consensus guest picker, time/turn budgets, SQLite store, CLI demo, and tests
- [x] 2026-11-27 Phase 2: Read-only CLI adapters (Claude, Codex, Gemini, OpenCode, OpenAI HTTP) + disposable git worktree guard (`townhall/cli_adapters.py`, `townhall/sandbox.py`, `townhall run`, `townhall adapters`, `tests/test_cli_adapters.py`)
- [ ] Phase 3: Standalone mini-web UI + recurring schedule runner (weekly project reviews)
  Add an optional lightweight web viewer (`townhall serve`) and a simple scheduler for recurring sessions (e.g. weekly repo health reviews respecting allowed hours, duration limits, and cooperative cancellation), saving every run to `TownHallStore`. Verify schedule selection, budget cutoff, and HTTP views in `tests/test_scheduler.py`.
  Recommended: codex, gpt-5.6, medium
- [ ] Phase 4: Integrate `/townhall` into Huo (`Marcobm1/baton`)
  Mount Town Hall inside Huo as `/townhall` and `/townhall/{id}` reusing Huo's auth/CSRF, model catalog (`Neon`, `Astra`, `Opus`, `OpenCode Go`), live SSE progress streaming, Telegram completion notices via Huo bot, and "Convert to task" / "Continue in /chat" bridges.
  Repo: Marcobm1/baton
  Recommended: claude-code, opus, medium

## Blockers
- None.

## Decisions
- 2026-11-27: Phase 2 CLI adapters remain 100% standard-library only (even `OpenAIHTTPAdapter` uses `urllib.request`) and `TownHallEngine.run()` automatically wraps git workspaces in a detached `disposable_worktree()` (`isolate_workspace=True`) so no participant—including `agy`—can mutate the owner's working tree or create stray branches.
- 2026-11-27 (Marco): Build Town Hall as a standalone open-source Python library (`Marcobm1/townhall`) first, then mount it inside Huo (`/townhall`).
- 2026-11-27: Keep the `townhall/` core standard-library only (zero runtime dependencies) so it embeds cleanly into Huo or any other host without version conflicts.
- 2026-11-27: Default to 1 cross-critique round in `council` mode (configurable to 2) to balance quality against 5-hour quota windows, with a blind parallel Round 1 to avoid anchoring/sycophancy and a user-chosen moderator for the final synthesis.
- 2026-11-27: Support both structured `council` debates (with consensus selection of a 4th OpenCode guest model via majority → Borda count → deterministic tie-break) and time-boxed `open_floor` conversations that always conclude with a summary and conclusion when the clock or turn limit expires.

## Log
- 2026-11-27 (antigravity): Completed Phase 2 (`townhall/cli_adapters.py`, `townhall/sandbox.py`, `TownHallEngine` worktree isolation, `townhall run` & `townhall adapters`, `tests/test_cli_adapters.py`, `README.md`, `docs/architecture.md`).
- 2026-11-27 (antigravity): Recorded optional future "AI Village" free-roam timed mode with persistent memory and cost controls in `docs/ideas.md`.
- 2026-11-27 (antigravity): Bootstrapped `Marcobm1/townhall` with architecture design (`docs/architecture.md`), Phase 1 stdlib-only deliberation engine, Borda consensus picker, SQLite WAL store, Markdown exporter, CLI, CI workflow, and test suite.
