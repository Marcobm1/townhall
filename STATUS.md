---
name: Town Hall
status: active
priority: medium
summary: Open-source multi-agent Town Hall where AI models debate, choose a 4th guest model by consensus, and produce a full transcript, summary, and conclusion.
next: "Phase 4: Integrate `/townhall` into Huo (`Marcobm1/baton`)"
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
- 2026-11-27: Phase 3 — Added recurring schedule persistence (`schedules` table in `TownHallStore`) and `TownHallScheduler` (with `HH:MM-HH:MM` allowed hours, non-overlapping ticks, and `on_finished` hook), plus the zero-dependency standalone web UI & SSE server (`townhall/server.py`, `townhall serve`, `townhall schedule`), CSRF + XSS protections, and integration tests (`tests/test_scheduler_web.py`).
- 2026-11-27: Phase 2 — Implemented read-only one-shot CLI adapters (`ClaudeCLIAdapter`, `CodexCLIAdapter`, `GeminiCLIAdapter`, `OpenCodeCLIAdapter` with deny-first `townhall-ro` rules) + stdlib `OpenAIHTTPAdapter`, `sanitize_env()` secret stripping, automatic detached `disposable_worktree()` git guard wired into `TownHallEngine`, `townhall run` & `townhall adapters` CLI commands, and cross-platform tests (`tests/test_cli_adapters.py`).
- 2026-11-27: Phase 1 — Initialized open-source repository `Marcobm1/townhall` with architecture docs (`docs/architecture.md`), zero-dependency core engine (`townhall/engine.py`, `townhall/consensus.py`, `townhall/prompts.py`, `townhall/adapter.py`, `townhall/models.py`), SQLite WAL store + Markdown exporter (`townhall/store.py`), CLI (`townhall/cli.py`), hardened CI, and unit/integration tests.

## Next
- [x] 2026-11-27 Phase 1: Architecture, core deliberation engine (`council` + `open_floor` modes), Borda consensus guest picker, time/turn budgets, SQLite store, CLI demo, and tests
- [x] 2026-11-27 Phase 2: Read-only CLI adapters (Claude, Codex, Gemini, OpenCode, OpenAI HTTP) + disposable git worktree guard (`townhall/cli_adapters.py`, `townhall/sandbox.py`, `townhall run`, `townhall adapters`, `tests/test_cli_adapters.py`)
- [x] 2026-11-27 Phase 3: Standalone mini-web UI (`townhall serve`, SSE, CSRF, XSS escaping) + recurring schedule runner (`townhall/scheduler.py`, `townhall schedule`, `tests/test_scheduler_web.py`)
- [ ] Phase 4: Integrate `/townhall` into Huo (`Marcobm1/baton`)
  Mount Town Hall inside Huo as `/townhall` and `/townhall/{id}` reusing Huo's auth/CSRF, model catalog (`Neon`, `Astra`, `Opus`, `OpenCode Go`), live SSE progress streaming, Telegram completion notices via Huo bot, and "Convert to task" / "Continue in /chat" bridges.
  Repo: Marcobm1/baton
  Recommended: claude-code, opus, medium

## Blockers
- None.

## Decisions
- 2026-11-27: Phase 3 standalone web server (`townhall/server.py`) stays standard-library only (`http.server.ThreadingHTTPServer`), binds to `127.0.0.1` by default, enforces per-server CSRF tokens on all POST endpoints, escapes all HTML output, and streams live progress via SSE.
- 2026-11-27: Phase 2 CLI adapters remain 100% standard-library only (even `OpenAIHTTPAdapter` uses `urllib.request`) and `TownHallEngine.run()` automatically wraps git workspaces in a detached `disposable_worktree()` (`isolate_workspace=True`) so no participant—including `agy`—can mutate the owner's working tree or create stray branches.
- 2026-11-27 (Marco): Build Town Hall as a standalone open-source Python library (`Marcobm1/townhall`) first, then mount it inside Huo (`/townhall`).
- 2026-11-27: Keep the `townhall/` core standard-library only (zero runtime dependencies) so it embeds cleanly into Huo or any other host without version conflicts.
- 2026-11-27: Default to 1 cross-critique round in `council` mode (configurable to 2) to balance quality against 5-hour quota windows, with a blind parallel Round 1 to avoid anchoring/sycophancy and a user-chosen moderator for the final synthesis.
- 2026-11-27: Support both structured `council` debates (with consensus selection of a 4th OpenCode guest model via majority → Borda count → deterministic tie-break) and time-boxed `open_floor` conversations that always conclude with a summary and conclusion when the clock or turn limit expires.

## Log
- 2026-11-27 (antigravity): Completed Phase 3 (`townhall/scheduler.py`, `townhall/server.py`, `schedules` table in `townhall/store.py`, `townhall serve` & `townhall schedule` CLI commands, `tests/test_scheduler_web.py`, `README.md`, `docs/architecture.md`).
- 2026-11-27 (antigravity): Completed Phase 2 (`townhall/cli_adapters.py`, `townhall/sandbox.py`, `TownHallEngine` worktree isolation, `townhall run` & `townhall adapters`, `tests/test_cli_adapters.py`, `README.md`, `docs/architecture.md`).
- 2026-11-27 (antigravity): Recorded optional future "AI Village" free-roam timed mode with persistent memory and cost controls in `docs/ideas.md`.
- 2026-11-27 (antigravity): Bootstrapped `Marcobm1/townhall` with architecture design (`docs/architecture.md`), Phase 1 stdlib-only deliberation engine, Borda consensus picker, SQLite WAL store, Markdown exporter, CLI, CI workflow, and test suite.
