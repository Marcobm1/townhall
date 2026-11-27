"""Adapter protocol for connecting Town Hall to any AI CLI, HTTP server, or test double."""

from __future__ import annotations

import json
import time
from typing import Callable, Protocol, runtime_checkable

from .models import TurnRequest, TurnResult


@runtime_checkable
class ParticipantAdapter(Protocol):
    """Minimal contract every model backend implements (CLI, HTTP, or mock)."""

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """Execute a single read-only turn and return its result."""
        ...


class CallableAdapter:
    """Wrap any `(TurnRequest) -> str` callable as a ParticipantAdapter."""

    def __init__(self, fn: Callable[[TurnRequest], str]):
        self._fn = fn

    def run_turn(self, request: TurnRequest) -> TurnResult:
        t0 = time.monotonic()
        now = time.time()
        try:
            text = str(self._fn(request))
            return TurnResult(
                participant_id=request.participant.id,
                participant_name=request.participant.name,
                model=request.participant.model,
                phase=request.phase,
                round_num=request.round_num,
                content=text,
                duration_seconds=time.monotonic() - t0,
                created_at=now,
            )
        except Exception as exc:
            return TurnResult(
                participant_id=request.participant.id,
                participant_name=request.participant.name,
                model=request.participant.model,
                phase=request.phase,
                round_num=request.round_num,
                content="",
                duration_seconds=time.monotonic() - t0,
                error=str(exc) or exc.__class__.__name__,
                created_at=now,
            )


class DemoAdapter:
    """Deterministic offline adapter for `townhall demo` and smoke testing.

    Simulates realistic multi-model perspectives (Gemini, Codex, Claude, and
    the consensus OpenCode guest) without making any network calls or burning
    provider quotas.
    """

    def __init__(self, *, guest_preferences: dict[str, tuple[str, str, str]] | None = None):
        self._prefs = guest_preferences or {
            "gemini": (
                "opencode-go/deepseek-v4-pro",
                "opencode-go/qwen4-max",
                "Strong architectural reasoning and independent failure-mode analysis.",
            ),
            "codex": (
                "opencode-go/deepseek-v4-pro",
                "opencode-go/kimi-k3.1-pro",
                "Solid code-level verification and pragmatic testing focus.",
            ),
            "claude": (
                "opencode-go/qwen4-max",
                "opencode-go/deepseek-v4-pro",
                "Clear synthesis and strong multilingual UX feedback.",
            ),
        }

    def run_turn(self, request: TurnRequest) -> TurnResult:
        t0 = time.monotonic()
        now = time.time()
        pid = request.participant.id
        pname = request.participant.name

        if request.phase == "vote_guest":
            primary, secondary, reason = self._prefs.get(
                pid,
                ("opencode-go/deepseek-v4-pro", "opencode-go/qwen4-max", "Balanced technical review."),
            )
            content = json.dumps(
                {"primary": primary, "secondary": secondary, "reason": reason},
                ensure_ascii=False,
            )
        elif request.phase == "independent":
            content = (
                f"[{pname}] Independent review of the request:\n"
                f"- Strength: clear core foundation and well-scoped modules.\n"
                f"- Gap identified by {pname}: add explicit end-to-end verification and clearer onboarding copy.\n"
                f"- Priority: ship the smallest high-impact improvement first, guarded by automated tests."
            )
        elif request.phase == "debate":
            content = (
                f"[{pname}] Cross-critique (round {request.round_num}):\n"
                f"- Agree: prioritizing automated verification and cleaner UX aligns across the table.\n"
                f"- Challenge: avoid over-engineering early abstractions before real usage confirms the bottleneck.\n"
                f"- Updated stance: keep a single minimal milestone with measurable acceptance criteria."
            )
        elif request.phase == "open_floor":
            content = (
                f"[{pname}] Turn {request.round_num}: building on the conversation, "
                f"let's make sure the workflow stays fast, transparent, and easy to audit."
            )
        elif request.phase == "synthesis":
            content = json.dumps(
                {
                    "summary": (
                        "All 4 models reviewed the topic independently, exchanged targeted critiques, "
                        "and converged on shipping a focused, test-guarded next milestone."
                    ),
                    "conclusion": (
                        "Prioritize clarity, automated end-to-end verification, and incremental delivery "
                        "before expanding scope."
                    ),
                    "agreements": [
                        "Keep the architecture modular and read-only during review workflows.",
                        "Add explicit automated verification for the highest-risk user journeys.",
                        "Improve first-run clarity and onboarding documentation.",
                    ],
                    "disagreements": [
                        "Timing of broader abstractions: wait for real usage data before generalizing.",
                    ],
                    "action_items": [
                        "Define measurable acceptance checks for the next milestone.",
                        "Implement the highest-impact UX/clarity polish first.",
                        "Schedule a follow-up weekly Town Hall review once deployed.",
                    ],
                },
                ensure_ascii=False,
            )
        else:
            content = f"[{pname}] Completed phase {request.phase}."

        return TurnResult(
            participant_id=pid,
            participant_name=pname,
            model=request.participant.model,
            phase=request.phase,
            round_num=request.round_num,
            content=content,
            duration_seconds=time.monotonic() - t0,
            created_at=now,
        )
