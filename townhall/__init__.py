"""Town Hall — open-source multi-agent deliberation engine."""

from .adapter import CallableAdapter, DemoAdapter, ParticipantAdapter
from .cli_adapters import (
    ClaudeCLIAdapter,
    CodexCLIAdapter,
    GeminiCLIAdapter,
    OpenAIHTTPAdapter,
    OpenCodeCLIAdapter,
    build_default_adapters,
    discover_adapters,
    opencode_readonly_config,
    sanitize_env,
)
from .consensus import parse_guest_vote, resolve_consensus
from .engine import TownHallEngine
from .models import (
    Conclusion,
    ConsensusSelection,
    GuestVote,
    Participant,
    SessionConfig,
    SessionEvent,
    SessionRecord,
    TurnRequest,
    TurnResult,
)
from .sandbox import WorkspaceContext, disposable_worktree
from .store import TownHallStore, render_markdown

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "CallableAdapter",
    "ClaudeCLIAdapter",
    "CodexCLIAdapter",
    "Conclusion",
    "ConsensusSelection",
    "DemoAdapter",
    "GeminiCLIAdapter",
    "GuestVote",
    "OpenAIHTTPAdapter",
    "OpenCodeCLIAdapter",
    "Participant",
    "ParticipantAdapter",
    "SessionConfig",
    "SessionEvent",
    "SessionRecord",
    "TownHallEngine",
    "TownHallStore",
    "TurnRequest",
    "TurnResult",
    "WorkspaceContext",
    "build_default_adapters",
    "discover_adapters",
    "disposable_worktree",
    "opencode_readonly_config",
    "parse_guest_vote",
    "render_markdown",
    "resolve_consensus",
    "sanitize_env",
]
