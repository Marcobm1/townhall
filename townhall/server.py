"""Standalone zero-dependency web UI & SSE server for Town Hall (`townhall serve`).

Security invariants:
  - Binds to `127.0.0.1` by default (never exposes the machine to LAN unless asked).
  - Escapes every user/model string with `html.escape(..., quote=True)`.
  - Protects every state-changing POST route with a per-server CSRF token.
  - Emits `X-Content-Type-Options: nosniff` and a strict CSP header.
"""

from __future__ import annotations

import html
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any

from .adapter import DemoAdapter, ParticipantAdapter
from .cli_adapters import build_default_adapters, discover_adapters
from .engine import TownHallEngine
from .models import Participant, ScheduleSpec, SessionConfig, SessionEvent, SessionRecord
from .scheduler import TownHallScheduler
from .store import TownHallStore, render_markdown

DEFAULT_GUEST_POOL = [
    "opencode-go/deepseek-v4-pro",
    "opencode-go/qwen4-max",
    "opencode-go/kimi-k3.1-pro",
    "opencode-go/glm-5.3",
]


def _default_participants() -> list[Participant]:
    return [
        Participant(id="gemini", name="Gemini", provider="gemini", model="gemini-4-neon", role="core"),
        Participant(id="codex", name="Codex", provider="codex", model="gpt-5.6-astra", role="core"),
        Participant(id="claude", name="Claude", provider="claude-code", model="claude-opus", role="core"),
    ]


def _esc(val: object) -> str:
    return html.escape(str(val or ""), quote=True)


def _fmt_time(ts: float) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


@dataclass
class _ActiveRun:
    session_id: str
    topic: str
    mode: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    cond: threading.Condition = field(default_factory=threading.Condition)

    def push(self, ev: SessionEvent) -> None:
        with self.cond:
            self.events.append(ev.to_dict())
            if ev.type in ("session_completed", "session_cancelled", "session_failed"):
                self.done = True
            self.cond.notify_all()

    def mark_done(self) -> None:
        with self.cond:
            self.done = True
            self.cond.notify_all()


class TownHallRuntime:
    """Shared application state for the standalone server (sessions, SSE, scheduler)."""

    def __init__(
        self,
        store: TownHallStore,
        *,
        adapter: ParticipantAdapter | dict[str, ParticipantAdapter] | None = None,
        demo: bool = False,
        poll_seconds: float = 60.0,
    ):
        self.store = store
        self.demo = demo
        self.adapter = adapter if adapter is not None else (
            DemoAdapter() if demo else build_default_adapters()
        )
        self.csrf_token = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._active: dict[str, _ActiveRun] = {}
        self.scheduler = TownHallScheduler(self.store, self.adapter)
        self._poll_seconds = poll_seconds

    def start_background(self) -> None:
        self.scheduler.start_background(poll_seconds=self._poll_seconds)

    def stop(self) -> None:
        self.scheduler.stop()
        with self._lock:
            for run in self._active.values():
                run.cancel_event.set()
                run.mark_done()

    def active_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"id": r.session_id, "topic": r.topic, "mode": r.mode, "done": r.done}
                for r in self._active.values()
                if not r.done
            ]

    def get_active(self, session_id: str) -> _ActiveRun | None:
        with self._lock:
            return self._active.get(session_id)

    def start_session(self, config: SessionConfig) -> str:
        sid = uuid.uuid4().hex[:12]
        run = _ActiveRun(session_id=sid, topic=config.topic.strip(), mode=config.mode)
        # Seed a running stub in SQLite immediately so /sessions/{id} works right away.
        stub = SessionRecord(
            id=sid,
            topic=config.topic.strip(),
            mode=config.mode,
            status="running",
            workspace=config.workspace,
            language=config.language,
            participants=list(config.participants),
            started_at=time.time(),
        )
        self.store.save(stub)
        with self._lock:
            self._active[sid] = run

        def _worker() -> None:
            try:
                engine = TownHallEngine(self.adapter, on_event=run.push)
                rec = engine.run(config, session_id=sid, cancel_event=run.cancel_event)
                self.store.save(rec)
            except Exception as exc:
                stub.status = "failed"
                stub.error = str(exc) or exc.__class__.__name__
                stub.finished_at = time.time()
                self.store.save(stub)
            finally:
                run.mark_done()

        threading.Thread(target=_worker, name=f"townhall-{sid}", daemon=True).start()
        return sid

    def cancel_session(self, session_id: str) -> bool:
        run = self.get_active(session_id)
        if run is None or run.done:
            return False
        run.cancel_event.set()
        return True

    def trigger_schedule_now(self, schedule_id: str) -> str | None:
        spec = self.store.get_schedule(schedule_id)
        if spec is None:
            return None
        sid = self.start_session(spec.to_session_config())
        self.store.mark_schedule_run(spec.id, time.time(), sid)
        return sid


# ----------------------------------------------------------- HTML views
_CSS = """
:root { color-scheme: light dark; --bg:#0c0c0d; --panel:#161618; --panel2:#1e1e21; --line:#2a2a2e;
  --ink:#ececef; --muted:#a1a1a8; --accent:#34d399; --warn:#fbbf24; --bad:#ef7d74; }
@media (prefers-color-scheme: light) {
  :root { --bg:#eef1f5; --panel:#ffffff; --panel2:#f3f5f8; --line:#e2e6ec;
    --ink:#1a2230; --muted:#5c6677; --accent:#059669; --warn:#b45309; --bad:#b3352f; }
}
* { box-sizing: border-box; }
body { margin:0; font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; background:var(--bg); color:var(--ink); }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
.wrap { max-width: 980px; margin: 0 auto; padding: 1.5rem 1.1rem 3rem; }
header { display:flex; align-items:center; justify-content:space-between; gap:1rem; padding-bottom:1rem; border-bottom:1px solid var(--line); margin-bottom:1.4rem; }
.brand { font-weight:700; font-size:1.15rem; color:var(--ink); }
.pill { display:inline-block; padding:.15rem .55rem; border-radius:999px; font:600 .72rem/1.4 ui-monospace, monospace; background:var(--panel2); border:1px solid var(--line); color:var(--muted); }
.pill.ok { color:var(--accent); } .pill.warn { color:var(--warn); } .pill.bad { color:var(--bad); }
.grid { display:grid; grid-template-columns: 1fr; gap:1.2rem; }
@media (min-width: 860px) { .grid-2 { display:grid; grid-template-columns: 1.25fr 1fr; gap:1.2rem; } }
.card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:1.1rem 1.2rem; }
h1 { margin:.2rem 0 .8rem; font-size:1.45rem; } h2 { margin:0 0 .8rem; font-size:1.05rem; } h3 { margin:.9rem 0 .4rem; font-size:.92rem; }
label { display:block; font-size:.82rem; color:var(--muted); margin:.65rem 0 .25rem; }
input[type=text], input[type=number], textarea, select {
  width:100%; padding:.52rem .65rem; border-radius:8px; border:1px solid var(--line);
  background:var(--panel2); color:var(--ink); font:inherit;
}
textarea { min-height:82px; resize:vertical; }
.row { display:flex; flex-wrap:wrap; gap:.65rem; align-items:center; }
.row > * { flex:1; min-width:120px; }
button, .btn { display:inline-flex; align-items:center; justify-content:center; gap:.35rem; padding:.5rem .9rem;
  border-radius:8px; border:1px solid var(--line); background:var(--panel2); color:var(--ink); font:600 .86rem/1.2 inherit; cursor:pointer; }
button.primary { background:var(--accent); color:#0b1512; border-color:transparent; }
button.danger { color:var(--bad); }
.list { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:.55rem; }
.item { display:flex; justify-content:space-between; align-items:flex-start; gap:.8rem; padding:.75rem .85rem;
  border-radius:9px; background:var(--panel2); border:1px solid var(--line); }
.muted { color:var(--muted); font-size:.84rem; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size:.82rem; }
pre.turn { white-space:pre-wrap; word-wrap:break-word; margin:.4rem 0 0; font:14px/1.5 system-ui, sans-serif; }
details { border:1px solid var(--line); border-radius:9px; padding:.6rem .8rem; background:var(--panel2); margin:.5rem 0; }
summary { cursor:pointer; font-weight:600; }
ul.clean { margin:.3rem 0 .6rem 1.2rem; padding:0; }
.live-log { font:13px/1.5 ui-monospace, monospace; background:var(--panel2); border:1px solid var(--line); border-radius:8px; padding:.7rem; min-height:70px; }
"""


def _layout(title: str, body: str, *, mode_badge: str = "") -> bytes:
    badge = f'<span class="pill">{_esc(mode_badge)}</span>' if mode_badge else ""
    doc = (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_esc(title)} · Town Hall</title><style>{_CSS}</style></head><body>"
        "<div class='wrap'><header>"
        f"<a class='brand' href='/'>Town Hall</a><div class='row' style='flex:0'>{badge}</div>"
        f"</header>{body}</div></body></html>"
    )
    return doc.encode("utf-8")


def _status_class(status: str) -> str:
    return {"completed": "ok", "running": "warn", "cancelled": "bad", "failed": "bad"}.get(status, "")


def _render_home(rt: TownHallRuntime) -> bytes:
    csrf = _esc(rt.csrf_token)
    sessions = rt.store.list_sessions(limit=25)
    schedules = rt.store.list_schedules()
    adapters = discover_adapters()
    adapter_pills = " ".join(
        f"<span class='pill {'ok' if path else ''}'>{_esc(name)}: {'ok' if path else '—'}</span>"
        for name, path in adapters.items()
    )

    sess_items = []
    for s in sessions:
        guest = f" · 4º: <code>{_esc(s['guest_model'])}</code>" if s["guest_model"] else ""
        sess_items.append(
            "<li class='item'><div>"
            f"<a href='/sessions/{_esc(s['id'])}'><strong>{_esc(s['topic'][:120])}</strong></a>"
            f"<div class='muted'>{_esc(_fmt_time(s['started_at']))} · {_esc(s['mode'])}{guest}</div>"
            "</div>"
            f"<span class='pill {_status_class(s['status'])}'>{_esc(s['status'])}</span></li>"
        )
    sessions_html = (
        f"<ul class='list'>{''.join(sess_items)}</ul>"
        if sess_items
        else "<p class='muted'>Aún no hay sesiones guardadas. Lanza la primera arriba.</p>"
    )

    sched_items = []
    for sc in schedules:
        last_link = (
            f" · última: <a href='/sessions/{_esc(sc.last_session_id)}'>{_esc(sc.last_session_id)}</a>"
            if sc.last_session_id
            else ""
        )
        hours = f" · horario {_esc(sc.allowed_hours)}" if sc.allowed_hours else ""
        toggle_label = "Pausar" if sc.enabled else "Activar"
        sched_items.append(
            "<li class='item'><div>"
            f"<strong>{_esc(sc.name)}</strong> "
            f"<span class='pill {'ok' if sc.enabled else ''}'>{'activa' if sc.enabled else 'pausada'}</span>"
            f"<div class='muted'>{_esc(sc.topic[:100])} · cada {sc.interval_hours:g}h{hours}{last_link}</div>"
            "</div><div class='row' style='flex:0;gap:.35rem'>"
            f"<form method='post' action='/schedules/{_esc(sc.id)}/run'><input type='hidden' name='csrf' value='{csrf}'><button type='submit'>Ejecutar</button></form>"
            f"<form method='post' action='/schedules/{_esc(sc.id)}/toggle'><input type='hidden' name='csrf' value='{csrf}'><button type='submit'>{toggle_label}</button></form>"
            f"<form method='post' action='/schedules/{_esc(sc.id)}/delete'><input type='hidden' name='csrf' value='{csrf}'><button type='submit' class='danger'>Borrar</button></form>"
            "</div></li>"
        )
    schedules_html = (
        f"<ul class='list'>{''.join(sched_items)}</ul>"
        if sched_items
        else "<p class='muted'>Sin programaciones todavía (por ejemplo, una revisión semanal de proyectos).</p>"
    )

    body = f"""
<div class='grid-2'>
  <section class='card'>
    <h2>Nueva sesión de Town Hall</h2>
    <form method='post' action='/sessions'>
      <input type='hidden' name='csrf' value='{csrf}'>
      <label for='topic'>Pregunta o tarea de revisión</label>
      <textarea id='topic' name='topic' required placeholder='Ej.: Revisa el estado de la página y dime qué falta prioritariamente...'></textarea>
      <label for='workspace'>Carpeta / repositorio local (opcional, siempre solo lectura)</label>
      <input id='workspace' name='workspace' type='text' placeholder='/ruta/al/proyecto'>
      <div class='row'>
        <div>
          <label for='mode'>Modo</label>
          <select id='mode' name='mode'>
            <option value='council'>Consejo (debate + 4º por consenso)</option>
            <option value='open_floor'>Ágora libre (con reloj)</option>
          </select>
        </div>
        <div>
          <label for='rounds'>Rondas crítica</label>
          <input id='rounds' name='rounds' type='number' min='0' max='3' value='1'>
        </div>
        <div>
          <label for='max_seconds'>Límite (seg.)</label>
          <input id='max_seconds' name='max_seconds' type='number' min='10' max='3600' value='600'>
        </div>
        <div>
          <label for='moderator'>Moderador</label>
          <select id='moderator' name='moderator'>
            <option value='claude'>Claude</option>
            <option value='gemini'>Gemini</option>
            <option value='codex'>Codex</option>
          </select>
        </div>
      </div>
      <div style='margin-top:1rem;display:flex;justify-content:space-between;align-items:center;gap:.6rem;flex-wrap:wrap'>
        <div>{adapter_pills}</div>
        <button type='submit' class='primary'>Iniciar Town Hall</button>
      </div>
    </form>
  </section>

  <section class='card'>
    <h2>Programar revisión periódica</h2>
    <form method='post' action='/schedules'>
      <input type='hidden' name='csrf' value='{csrf}'>
      <label for='s-name'>Nombre</label>
      <input id='s-name' name='name' type='text' required placeholder='Revisión semanal de Huo'>
      <label for='s-topic'>Pregunta / objetivo</label>
      <input id='s-topic' name='topic' type='text' required placeholder='Revisa STATUS.md, tests y qué falta'>
      <label for='s-ws'>Carpeta / repo (opcional)</label>
      <input id='s-ws' name='workspace' type='text' placeholder='/ruta/al/proyecto'>
      <div class='row'>
        <div>
          <label for='s-hours'>Cada (horas)</label>
          <input id='s-hours' name='interval_hours' type='number' min='1' step='1' value='168'>
        </div>
        <div>
          <label for='s-window'>Franja horaria</label>
          <input id='s-window' name='allowed_hours' type='text' placeholder='09:00-21:00'>
        </div>
        <div>
          <label for='s-max'>Tope (seg.)</label>
          <input id='s-max' name='max_seconds' type='number' min='30' max='3600' value='600'>
        </div>
      </div>
      <div style='margin-top:1rem;text-align:right'>
        <button type='submit'>Guardar programación</button>
      </div>
    </form>
  </section>
</div>

<section class='card' style='margin-top:1.2rem'>
  <h2>Historial de sesiones</h2>
  {sessions_html}
</section>

<section class='card' style='margin-top:1.2rem'>
  <h2>Programaciones recurrentes</h2>
  {schedules_html}
</section>
"""
    return _layout("Inicio", body, mode_badge="demo" if rt.demo else "live")


def _render_session(rt: TownHallRuntime, rec: SessionRecord) -> bytes:
    csrf = _esc(rt.csrf_token)
    parts = ", ".join(f"{_esc(p.name)} (<code>{_esc(p.model)}</code>)" for p in rec.participants)
    cancel_btn = ""
    live_box = ""
    if rec.status == "running":
        cancel_btn = (
            f"<form method='post' action='/sessions/{_esc(rec.id)}/cancel' style='display:inline'>"
            f"<input type='hidden' name='csrf' value='{csrf}'>"
            "<button type='submit' class='danger'>Cancelar</button></form>"
        )
        live_box = (
            "<section class='card' style='margin-bottom:1.1rem'><h2>Progreso en vivo</h2>"
            "<div id='live' class='live-log'>Conectando al flujo de eventos…</div>"
            f"<script>(function(){{var el=document.getElementById('live');"
            f"var es=new EventSource('/api/sessions/{_esc(rec.id)}/events');"
            "es.onmessage=function(e){var d=JSON.parse(e.data);"
            "el.textContent += '\\n['+d.type+'] ' + JSON.stringify(d.payload||{});"
            "if(d.type==='session_completed'||d.type==='session_cancelled'||d.type==='session_failed')"
            "{es.close();location.reload();}};})();</script></section>"
        )

    concl_html = ""
    if rec.conclusion:
        c = rec.conclusion
        ag = "".join(f"<li>{_esc(x)}</li>" for x in c.agreements)
        dis = "".join(f"<li>{_esc(x)}</li>" for x in c.disagreements)
        act = "".join(f"<li>{_esc(x)}</li>" for x in c.action_items)
        concl_html = (
            "<section class='card' style='margin-bottom:1.1rem'>"
            "<h2>Conclusión</h2>"
            f"<p>{_esc(c.conclusion)}</p>"
            "<h3>Resumen ejecutivo</h3>"
            f"<p class='muted'>{_esc(c.summary)}</p>"
            + (f"<h3>Acuerdos</h3><ul class='clean'>{ag}</ul>" if ag else "")
            + (f"<h3>Discrepancias abiertas</h3><ul class='clean'>{dis}</ul>" if dis else "")
            + (f"<h3>Plan de acción priorizado</h3><ol class='clean'>{act}</ol>" if act else "")
            + f"<p class='muted'>Moderador: {_esc(c.moderator_name)}</p></section>"
        )

    guest_html = ""
    if rec.guest_selection:
        g = rec.guest_selection
        votes_li = "".join(
            f"<li><strong>{_esc(v.voter_name)}:</strong> <code>{_esc(v.primary or 'inválido')}</code>"
            + (f" (2ª: <code>{_esc(v.secondary)}</code>)" if v.secondary else "")
            + (f" — {_esc(v.reason)}" if v.reason else "")
            + "</li>"
            for v in g.votes
        )
        guest_html = (
            "<section class='card' style='margin-bottom:1.1rem'>"
            f"<h2>Elección del 4º modelo invitado · <code>{_esc(g.chosen_model)}</code> "
            f"<span class='pill'>{_esc(g.method)}</span></h2>"
            f"<ul class='clean'>{votes_li}</ul></section>"
        )

    turns_html = []
    for t in rec.turns:
        if t.phase == "vote_guest" or (t.phase == "synthesis" and rec.conclusion is not None):
            continue
        body_txt = f"Error: {t.error}" if t.error else t.content
        turns_html.append(
            "<details open>"
            f"<summary>[{_esc(t.phase)} · ronda {t.round_num}] {_esc(t.participant_name)} "
            f"<span class='mono muted'>({_esc(t.model)})</span></summary>"
            f"<pre class='turn'>{_esc(body_txt)}</pre></details>"
        )
    transcript_section = (
        "<section class='card'><h2>Conversación completa</h2>"
        + ("".join(turns_html) if turns_html else "<p class='muted'>Sin turnos registrados todavía.</p>")
        + "</section>"
    )

    budget_pill = " <span class='pill warn'>límite de tiempo</span>" if rec.budget_reached else ""
    body = f"""
<div style='display:flex;justify-content:space-between;align-items:center;gap:.8rem;flex-wrap:wrap;margin-bottom:1rem'>
  <div>
    <a href='/' class='muted'>← Volver</a>
    <h1>{_esc(rec.topic)}</h1>
    <div class='muted'>
      <span class='pill {_status_class(rec.status)}'>{_esc(rec.status)}</span>{budget_pill}
      · {_esc(rec.mode)} · {_esc(_fmt_time(rec.started_at))} · {parts}
    </div>
  </div>
  <div class='row' style='flex:0'>
    {cancel_btn}
    <a class='btn' href='/sessions/{_esc(rec.id)}.md'>Markdown</a>
    <a class='btn' href='/api/sessions/{_esc(rec.id)}'>JSON</a>
  </div>
</div>
{live_box}{concl_html}{guest_html}{transcript_section}
"""
    return _layout(rec.topic[:48], body, mode_badge="demo" if rt.demo else "live")


def make_handler(runtime: TownHallRuntime) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _fmt: str, *_args: object) -> None:
            pass

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, data: Any) -> None:
            self._send_bytes(
                status,
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _redirect(self, location: str) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

        def _read_form(self) -> dict[str, str]:
            length = min(int(self.headers.get("Content-Length", "0") or 0), 256_000)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)
            return {k: (v[0] if v else "") for k, v in parsed.items()}

        def _check_csrf(self, form: dict[str, str]) -> bool:
            token = form.get("csrf") or self.headers.get("X-TownHall-CSRF", "")
            return secrets.compare_digest(token, runtime.csrf_token)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._send_json(200, {"ok": True, "demo": runtime.demo, "active": len(runtime.active_runs())})
                return
            if path == "/":
                self._send_bytes(200, _render_home(runtime), "text/html; charset=utf-8")
                return
            if path == "/api/sessions":
                self._send_json(200, {"sessions": runtime.store.list_sessions(limit=50)})
                return
            if path == "/api/schedules":
                self._send_json(200, {"schedules": [s.to_dict() for s in runtime.store.list_schedules()]})
                return
            if path.startswith("/api/sessions/") and path.endswith("/events"):
                sid = path[len("/api/sessions/"):-len("/events")].strip("/")
                self._stream_events(sid)
                return
            if path.startswith("/api/sessions/"):
                sid = path[len("/api/sessions/"):].strip("/")
                rec = runtime.store.get(sid)
                if rec is None:
                    self._send_json(404, {"error": "session not found"})
                else:
                    self._send_json(200, rec.to_dict())
                return
            if path.startswith("/sessions/") and path.endswith(".md"):
                sid = path[len("/sessions/"):-3].strip("/")
                rec = runtime.store.get(sid)
                if rec is None:
                    self._send_bytes(404, b"Not found", "text/plain; charset=utf-8")
                else:
                    self._send_bytes(200, render_markdown(rec).encode("utf-8"), "text/markdown; charset=utf-8")
                return
            if path.startswith("/sessions/"):
                sid = path[len("/sessions/"):].strip("/")
                rec = runtime.store.get(sid)
                if rec is None:
                    self._send_bytes(404, _layout("No encontrada", "<p>Sesión no encontrada.</p>"), "text/html; charset=utf-8")
                else:
                    self._send_bytes(200, _render_session(runtime, rec), "text/html; charset=utf-8")
                return
            self._send_bytes(404, _layout("404", "<p>Ruta no encontrada.</p>"), "text/html; charset=utf-8")

        def _stream_events(self, sid: str) -> None:
            run = runtime.get_active(sid)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if run is None:
                rec = runtime.store.get(sid)
                ev = {
                    "type": f"session_{rec.status}" if rec else "session_failed",
                    "session_id": sid,
                    "payload": {},
                }
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode("utf-8"))
                self.wfile.flush()
                return
            idx = 0
            while True:
                with run.cond:
                    while idx >= len(run.events) and not run.done:
                        run.cond.wait(timeout=1.0)
                    batch = run.events[idx:]
                    idx = len(run.events)
                    done = run.done
                try:
                    for ev in batch:
                        self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except OSError:
                    return
                if done and idx >= len(run.events):
                    return

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            form = self._read_form()
            if not self._check_csrf(form):
                self._send_json(403, {"error": "invalid csrf token"})
                return

            if path == "/sessions":
                topic = (form.get("topic") or "").strip()
                if not topic:
                    self._send_json(400, {"error": "topic is required"})
                    return
                mode = form.get("mode") if form.get("mode") in ("council", "open_floor") else "council"
                rounds = max(0, min(3, int(form.get("rounds") or 1)))
                max_seconds = max(10.0, min(3600.0, float(form.get("max_seconds") or 600.0)))
                moderator = (form.get("moderator") or "claude").strip()
                workspace = (form.get("workspace") or "").strip()
                cfg = SessionConfig(
                    topic=topic,
                    participants=_default_participants(),
                    mode=mode,
                    workspace=workspace,
                    guest_pool=list(DEFAULT_GUEST_POOL),
                    debate_rounds=rounds,
                    max_duration_seconds=max_seconds,
                    moderator_id=moderator,
                    isolate_workspace=not runtime.demo,
                )
                sid = runtime.start_session(cfg)
                self._redirect(f"/sessions/{sid}")
                return

            if path.startswith("/sessions/") and path.endswith("/cancel"):
                sid = path[len("/sessions/"):-len("/cancel")].strip("/")
                runtime.cancel_session(sid)
                self._redirect(f"/sessions/{sid}")
                return

            if path == "/schedules":
                name = (form.get("name") or "").strip()
                topic = (form.get("topic") or "").strip()
                if not name or not topic:
                    self._send_json(400, {"error": "name and topic are required"})
                    return
                interval_hours = max(1.0, float(form.get("interval_hours") or 168.0))
                max_seconds = max(30.0, min(3600.0, float(form.get("max_seconds") or 600.0)))
                spec = ScheduleSpec(
                    id=uuid.uuid4().hex[:10],
                    name=name,
                    topic=topic,
                    participants=_default_participants(),
                    workspace=(form.get("workspace") or "").strip(),
                    guest_pool=list(DEFAULT_GUEST_POOL),
                    max_duration_seconds=max_seconds,
                    moderator_id="claude",
                    interval_hours=interval_hours,
                    allowed_hours=(form.get("allowed_hours") or "").strip(),
                )
                runtime.store.save_schedule(spec)
                self._redirect("/")
                return

            if path.startswith("/schedules/") and path.endswith("/toggle"):
                sid = path[len("/schedules/"):-len("/toggle")].strip("/")
                spec = runtime.store.get_schedule(sid)
                if spec is not None:
                    runtime.store.set_schedule_enabled(sid, not spec.enabled)
                self._redirect("/")
                return

            if path.startswith("/schedules/") and path.endswith("/delete"):
                sid = path[len("/schedules/"):-len("/delete")].strip("/")
                runtime.store.delete_schedule(sid)
                self._redirect("/")
                return

            if path.startswith("/schedules/") and path.endswith("/run"):
                sid = path[len("/schedules/"):-len("/run")].strip("/")
                session_id = runtime.trigger_schedule_now(sid)
                self._redirect(f"/sessions/{session_id}" if session_id else "/")
                return

            self._send_json(404, {"error": "not found"})

    return Handler


def create_server(
    store: TownHallStore,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    adapter: ParticipantAdapter | dict[str, ParticipantAdapter] | None = None,
    demo: bool = False,
    poll_seconds: float = 60.0,
) -> tuple[http.server.ThreadingHTTPServer, TownHallRuntime]:
    runtime = TownHallRuntime(store, adapter=adapter, demo=demo, poll_seconds=poll_seconds)
    server = http.server.ThreadingHTTPServer((host, port), make_handler(runtime))
    return server, runtime
