"""Disposable git worktree guard so read-only deliberations never touch your working tree.

Why this exists:
  Even when CLIs are invoked in read-only modes, some tools (for example `agy`
  in default mode) can edit files inside the current directory without asking.
  Running a Town Hall session inside a detached temporary `git worktree` gives
  every participant a clean, real copy of `HEAD` while guaranteeing that the
  owner's working tree and branches remain untouched — any stray write is
  discarded as soon as the session ends.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

_NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}


@dataclass(frozen=True)
class WorkspaceContext:
    """Resolved workspace path passed to adapters during one session."""

    path: str
    original: str
    isolated: bool = False
    repo_root: str = ""


def _git_root(folder: Path) -> Path | None:
    try:
        res = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            stdin=subprocess.DEVNULL,
            **_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    root = res.stdout.strip()
    return Path(root) if root else None


def _has_head_commit(repo_root: Path) -> bool:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            stdin=subprocess.DEVNULL,
            **_NO_WINDOW,
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _rmtree_force(path: Path) -> None:
    """Delete a directory tree even when Windows marks `.git` pack files read-only."""

    def _clear_readonly(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            func(target)
        except OSError:
            pass

    for _ in range(6):
        if not path.exists():
            return
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=_clear_readonly)
            else:
                shutil.rmtree(path, onerror=_clear_readonly)
        except OSError:
            pass
        if not path.exists():
            return
        time.sleep(0.2)


def _cleanup_worktree(repo_root: Path, wt_dir: Path, parent_tmp: Path) -> None:
    for _ in range(5):
        try:
            subprocess.run(
                ["git", "-C", str(repo_root), "worktree", "remove", "--force", str(wt_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                stdin=subprocess.DEVNULL,
                **_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        if not wt_dir.exists():
            break
        time.sleep(0.2)
    if wt_dir.exists():
        _rmtree_force(wt_dir)
    if parent_tmp.exists():
        _rmtree_force(parent_tmp)
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "prune"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            stdin=subprocess.DEVNULL,
            **_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        pass


@contextlib.contextmanager
def disposable_worktree(
    workspace: str | Path,
    *,
    enabled: bool = True,
) -> Iterator[WorkspaceContext]:
    """Yield a detached disposable git worktree when `workspace` is inside a git repo.

    If `enabled` is False, `workspace` is empty, or `workspace` is not a git repo
    with a valid `HEAD`, yields the original path unchanged (`isolated=False`).
    """
    raw = str(workspace or "").strip()
    if not raw or not enabled:
        yield WorkspaceContext(path=raw, original=raw, isolated=False)
        return

    folder = Path(raw).resolve()
    if not folder.is_dir():
        yield WorkspaceContext(path=raw, original=raw, isolated=False)
        return

    repo_root = _git_root(folder)
    if repo_root is None or not _has_head_commit(repo_root):
        yield WorkspaceContext(path=str(folder), original=raw, isolated=False)
        return

    # Preserve relative subdirectory if the user targeted a folder inside the repo.
    try:
        rel_sub = folder.relative_to(repo_root.resolve())
    except ValueError:
        rel_sub = Path()

    parent_tmp = Path(tempfile.mkdtemp(prefix="townhall-wt-"))
    wt_dir = parent_tmp / "repo"
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "add", "--detach", str(wt_dir), "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            stdin=subprocess.DEVNULL,
            **_NO_WINDOW,
        )
        if res.returncode != 0 or not wt_dir.is_dir():
            _rmtree_force(parent_tmp)
            yield WorkspaceContext(path=str(folder), original=raw, isolated=False, repo_root=str(repo_root))
            return

        effective = (wt_dir / rel_sub) if str(rel_sub) not in ("", ".") else wt_dir
        yield WorkspaceContext(
            path=str(effective),
            original=raw,
            isolated=True,
            repo_root=str(repo_root),
        )
    finally:
        _cleanup_worktree(repo_root, wt_dir, parent_tmp)
