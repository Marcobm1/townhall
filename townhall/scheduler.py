"""Recurring schedule runner for Town Hall (e.g. weekly project health reviews)."""

from __future__ import annotations

import datetime as dt
import re
import threading
import time
from typing import Callable

from .adapter import ParticipantAdapter
from .engine import EventCallback, TownHallEngine
from .models import ScheduleSpec, SessionRecord
from .store import TownHallStore

_HOURS_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")

OnScheduleFinished = Callable[[ScheduleSpec, SessionRecord], None]


def in_allowed_hours(allowed_hours: str, now_dt: dt.datetime | None = None) -> bool:
    """Check whether `now_dt` falls inside an optional `HH:MM-HH:MM` window.

    Empty string means always allowed. Supports windows that cross midnight
    (e.g. `22:00-06:00`). Malformed windows fail closed (return False).
    """
    spec = (allowed_hours or "").strip()
    if not spec:
        return True
    m = _HOURS_RE.match(spec)
    if not m:
        return False
    sh, sm, eh, em = (int(x) for x in m.groups())
    if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
        return False
    cur_dt = now_dt or dt.datetime.now()
    cur = cur_dt.hour * 60 + cur_dt.minute
    start = sh * 60 + sm
    end = eh * 60 + em
    if start == end:
        return True
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def is_schedule_due(
    schedule: ScheduleSpec,
    *,
    now_ts: float | None = None,
    now_dt: dt.datetime | None = None,
) -> bool:
    """Return True when an enabled schedule is inside its allowed hours and its interval elapsed."""
    if not schedule.enabled:
        return False
    ts = time.time() if now_ts is None else float(now_ts)
    cur_dt = now_dt or dt.datetime.fromtimestamp(ts)
    if not in_allowed_hours(schedule.allowed_hours, cur_dt):
        return False
    if schedule.last_run_at <= 0:
        return True
    return (ts - schedule.last_run_at) >= (schedule.interval_hours * 3600.0)


class TownHallScheduler:
    """Evaluates stored `ScheduleSpec` rows and executes due sessions without overlap."""

    def __init__(
        self,
        store: TownHallStore,
        adapter: ParticipantAdapter | dict[str, ParticipantAdapter],
        *,
        on_event: EventCallback | None = None,
        on_finished: OnScheduleFinished | None = None,
    ):
        self.store = store
        self.adapter = adapter
        self._on_event = on_event
        self._on_finished = on_finished
        self._run_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run_schedule(
        self,
        schedule: ScheduleSpec,
        *,
        now_ts: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SessionRecord:
        """Run one schedule immediately, persisting both the session and the updated schedule timestamp."""
        engine = TownHallEngine(self.adapter, on_event=self._on_event)
        record = engine.run(schedule.to_session_config(), cancel_event=cancel_event)
        self.store.save(record)
        run_ts = time.time() if now_ts is None else float(now_ts)
        self.store.mark_schedule_run(schedule.id, run_ts, record.id)
        if self._on_finished is not None:
            try:
                self._on_finished(schedule, record)
            except Exception:
                pass
        return record

    def tick(
        self,
        *,
        now_ts: float | None = None,
        now_dt: dt.datetime | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[SessionRecord]:
        """Run all currently due schedules sequentially; skips immediately if another tick is running."""
        if not self._run_lock.acquire(blocking=False):
            return []
        try:
            ts = time.time() if now_ts is None else float(now_ts)
            cur_dt = now_dt or dt.datetime.fromtimestamp(ts)
            ran: list[SessionRecord] = []
            for sched in self.store.list_schedules():
                if cancel_event is not None and cancel_event.is_set():
                    break
                if is_schedule_due(sched, now_ts=ts, now_dt=cur_dt):
                    ran.append(self.run_schedule(sched, now_ts=ts, cancel_event=cancel_event))
            return ran
        finally:
            self._run_lock.release()

    def start_background(self, *, poll_seconds: float = 60.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                try:
                    self.tick(cancel_event=self._stop_event)
                except Exception:
                    pass
                self._stop_event.wait(max(1.0, float(poll_seconds)))

        self._thread = threading.Thread(target=_loop, name="townhall-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
