# Town Hall — Architecture & Design Decisions

Town Hall lets a group of AI models deliberate over a question or repository
and always returns three durable outputs: **full transcript**, **summary**, and
**conclusion**.

## 1. Core library + host integration (why a separate repo)

**Decision:** Build `townhall` as a standalone, standard-library-only Python
package (`Marcobm1/townhall`) and mount it inside Huo (`Marcobm1/baton`) in
Phase 4.

- **Alternatives considered:**
  - *Build directly inside Huo:* fastest initial wiring, but ties the protocol
    to Huo's FastAPI app and makes open-source reuse hard.
  - *Microservice over HTTP:* extra port, process lifecycle, and auth surface
    for no benefit on a single desktop PC.
- **Why library-first wins here:** `townhall` defines a tiny `ParticipantAdapter`
  protocol (`run_turn(TurnRequest) -> TurnResult`). Anyone can use it from CLI
  or Python with built-in CLI/HTTP adapters, while Huo can plug in its own
  catalog, `agent_env()`, SSE stream, Telegram notifications, and "Convert to
  task" button without duplicating the deliberation engine.
- **Trade-off:** Phase 2 needs lightweight one-shot CLI adapters in `townhall`,
  though they are far simpler than Huo's interactive `/chat` adapters because
  deliberation turns are read-only and non-interactive.

## 2. Preventing the 3 classic multi-agent failure modes

1. **Anchoring / sycophancy ("me too" bias):**
   - *Failure:* If Model B reads Model A's draft before forming its own opinion,
     Model B systematically echoes Model A's framing.
   - *Design:* **Phase 1 runs blind and in parallel.** All 4 participants form
     their initial diagnosis independently; only in **Phase 2 (cross-critique)**
     do they see the other 3 positions.
2. **Unintended repository mutations (security):**
   - *Failure:* Review sessions should never edit code, commit, or run
     destructive commands. While OpenCode (`baton-helper`), Claude Code, and
     Codex can enforce read-only tool policies, some CLIs (e.g. `agy` in
     default mode) can still edit files inside the working directory.
   - *Design (Phase 2):* When targeting a git repository, real CLI adapters run
     against a **disposable `git worktree`** checked out at `HEAD` and removed
     when the session ends. Even a misbehaving CLI cannot touch the user's real
     working tree.
3. **Runaway quota and wall-clock cost:**
   - *Failure:* Open-ended debates burn 5-hour quota windows and stall when one
     provider hangs.
   - *Design:* Hard budgets on every session (`max_duration_seconds`,
     `turn_timeout_seconds`, `debate_rounds` defaulting to `1`, `max_turns` in
     `open_floor`) plus cooperative cancellation (`threading.Event`). When the
     time budget expires, Town Hall stops opening new debate turns and skips
     straight to **synthesis** so the work already done still yields a clear
     summary and conclusion.

## 3. Consensus selection of the 4th OpenCode guest model (Phase 0)

In `council` mode, when `guest_pool` is non-empty:

1. Each core participant (e.g. Gemini, Codex, Claude) receives the user's topic
   and the exact list of allowed guest model IDs (for example, models included
   in OpenCode Go) and returns JSON with `primary`, `secondary`, and `reason`.
2. `resolve_consensus()` validates every vote strictly against `guest_pool`
   (hallucinated model IDs are ignored, never executed):
   - **Majority win:** if a candidate gets strictly more than half of valid
     primary votes (e.g. 2/3 or 3/3), it wins immediately.
   - **Borda count tie-break:** on a split vote (e.g. 1–1–1), each `primary`
     choice scores **2 points** and each distinct `secondary` choice scores
     **1 point**. The highest Borda score wins.
   - **Deterministic fallback:** remaining ties break by most primary votes,
     then alphabetical model ID (or first item in `guest_pool` if every voter
     failed to produce a valid candidate).

Every raw vote, parsed choice, and resolution method (`unanimous`, `majority`,
`borda`, `tie_break`, `fallback`) is persisted in SQLite so the user can always
see *why* that 4th model joined the table.

## 4. Deliberation protocols

### Mode A: `council` (structured debate & review)
- **Phase 0 (`vote_guest`):** Core models pick the 4th OpenCode guest in parallel.
- **Phase 1 (`independent`):** All 4 models analyze the topic blindly in parallel.
- **Phase 2 (`debate`):** `debate_rounds` (default `1`, max `2`) parallel rounds
  where each model critiques the other 3 and updates its position.
- **Phase 3 (`synthesis`):** The selected moderator (default: first core model
  or user-specified `moderator_id`) writes a structured JSON/Markdown closing
  report with: `summary`, `conclusion`, `agreements`, `disagreements`, and
  `action_items`.

### Mode B: `open_floor` (time-boxed open interaction)
- Participants take round-robin turns (optionally picking the 4th guest first if
  `guest_pool` is set) seeing the rolling conversation so far.
- Stops cleanly as soon as `max_turns` is reached, `max_duration_seconds`
  expires, or a cancellation signal arrives.
- Runs the same **synthesis** phase at the end so even free-form discussions
  produce a readable summary and takeaway list.

## 5. Persistence & streaming contract

- **SQLite in WAL mode (`townhall/store.py`):**
  - `sessions`: metadata, config JSON, status (`running`, `completed`,
    `cancelled`, `failed`), chosen guest model + rationale, final `summary`,
    `conclusion`, `agreements`, `disagreements`, `action_items`, timestamps.
  - `votes`: each voter's primary/secondary choice, reason, and validity flag.
  - `turns`: every phase/round/participant turn (prompt summary, content,
    duration, error if a single model failed while the rest continued).
- **Event stream (`on_event(SessionEvent)`):**
  - Emits typed events (`session_started`, `phase_started`, `guest_selected`,
    `turn_started`, `turn_completed`, `budget_reached`, `session_completed`,
    `session_cancelled`) so CLI progress bars and Huo's future SSE endpoint share
    one clean contract.

## 6. Roadmap

- **Phase 1 (done):** Core models, consensus picker, `council` + `open_floor`
  engine with budgets & cancellation, SQLite WAL store, Markdown export, CLI
  demo, tests.
- **Phase 2 (done):** Read-only one-shot CLI adapters (`claude`, `codex`, `agy`,
  `opencode`, stdlib OpenAI-compatible HTTP), `sanitize_env()` secret stripping,
  automatic detached `disposable_worktree()` guard, and `townhall run` /
  `townhall adapters`.
- **Phase 3:** Recurring scheduler (e.g. weekly project reviews) + minimal
  standalone web UI (`townhall serve`).
- **Phase 4:** Integration into Huo (`/townhall`) with live SSE, Huo bot
  Telegram notifications, and "Convert to task" / "Continue in /chat".
