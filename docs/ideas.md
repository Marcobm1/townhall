# Future ideas (optional follow-ups)

Optional ideas to explore **after** Phases 1–4 are complete and stable. None of
these are scheduled in `STATUS.md` yet.

## 1. "AI Village" free-roam mode (timed open world with persistent memory)

Inspired by *AI Village* / *Generative Agents*: activate Town Hall on demand
for a fixed window (for example **30 minutes**) where several models converse
freely without a rigid project-review prompt, follow emerging threads of
interest, and **record their own notes / learned knowledge** across turns and
sessions.

### How it would work
- **Shared notebook + per-agent memory:** alongside the raw transcript, models
  can append short, structured notes (e.g. a `memories` table in SQLite or a
  curated `knowledge.md` board) with facts, questions, hypotheses, or
  agreements they want to remember next time.
- **Calm pacing & hard clock:** one turn every 20–45 seconds (or a configurable
  turn cap) until the 30-minute timer ends, followed by an automatic chronicle /
  digest summarizing what happened and what new notes were stored.
- **Strict read-only safety:** still zero code edits, zero external side
  effects, and explicit allowlists if web search is ever opted in.

### Cost & quota control (why naive 30-minute loops get expensive)
- **The naive trap ($O(N^2)$ token growth):** if 4 frontier models (Opus,
  Astra, Neon) send the entire growing transcript on every turn for 30 minutes
  (~60–90 turns), input tokens grow quadratically and can burn a large chunk of
  a 5-hour quota window quickly.
- **How to keep it cheap (or nearly free):**
  1. **Sliding window + memory compaction ($O(1)$ prompt size):** pass only the
     last 6–8 messages plus the compact shared notebook/summary instead of the
     full raw history.
  2. **Mix local & fast/free models:** run local `llama-server` models on the
     host GPU (\$0) and fast/free OpenCode Go models (`deepseek-v4.1-flash`,
     `qwen3.8-flash`, `beam-free`, etc.) for day-to-day chatter, reserving a
     frontier model only as an optional periodic "chronicler" every 10 minutes
     or at the end.
  3. **Hard budgets:** explicit `max_duration_seconds`, `max_turns`, and a
     maximum token/character cap on the shared notebook.
