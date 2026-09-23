"""
Session Management for Harness.
Supports full conversation persistence, list, resume, fork, and markdown export.
"""
import os
import json
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

@dataclass
class Session:
    id: str
    title: str
    provider: str
    model: str
    mode: str
    permission: str
    thinking_effort: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    todos: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    total_tokens: int = 0
    # Workspace folder this session belongs to ("" for sessions saved before
    # workspaces were tracked). Sessions live in the global store but are
    # associated with the folder they were created in.
    workspace: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        return cls(**data)

class SessionManager:
    """Manages active and historical sessions.

    Sessions are stored globally under ``~/.harness/sessions/`` but each one is
    associated with the workspace folder it was created in (``workspace`` field,
    defaulting to the current working directory). Listing can filter by that
    association so a folder only shows its own conversation history.
    """

    def __init__(self, storage_dir: Optional[Path] = None, workspace: Optional[str] = None):
        self.storage_dir = Path(storage_dir) if storage_dir else (Path.home() / ".harness" / "sessions")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # Workspace folder new sessions will be associated with.
        self.workspace: str = str(Path(workspace).resolve()) if workspace else str(Path.cwd().resolve())

    def _file_path(self, session_id: str) -> Path:
        clean_id = session_id.replace("/", "_").replace("\\", "_")
        return self.storage_dir / f"{clean_id}.json"

    @staticmethod
    def _norm_workspace(workspace: Optional[str]) -> str:
        """Absolute, trailing-slash-free form of a workspace path ("" stays "")."""
        if not workspace:
            return ""
        try:
            return str(Path(workspace).expanduser().resolve())
        except Exception:
            return str(workspace).rstrip("/\\")

    @classmethod
    def matches_workspace(cls, session_workspace: str, workspace: str) -> bool:
        """True when a session belongs to ``workspace`` (untracked sessions
        match any workspace, so pre-workspace history is never hidden)."""
        if not cls._norm_workspace(session_workspace):
            return True
        if not workspace:
            return True
        return cls._norm_workspace(session_workspace) == cls._norm_workspace(workspace)

    def create(self, provider: str, model: str, mode: str, permission: str, thinking_effort: str, title: str = "New Session", workspace: Optional[str] = None) -> Session:
        sid = f"sess_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        session = Session(
            id=sid,
            title=title,
            provider=provider,
            model=model,
            mode=mode,
            permission=permission,
            thinking_effort=thinking_effort,
            workspace=self._norm_workspace(workspace or self.workspace),
        )
        self.save(session)
        return session

    def save(self, session: Session) -> None:
        session.updated_at = time.time()
        file_path = self._file_path(session.id)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(session.to_dict(), f, indent=2)

    def load(self, session_id: str) -> Optional[Session]:
        file_path = self._file_path(session_id)
        if not file_path.exists():
            # Try fuzzy match
            for p in self.storage_dir.glob("*.json"):
                if session_id in p.stem:
                    file_path = p
                    break
        if not file_path.exists():
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return Session.from_dict(data)
        except Exception:
            return None

    def list_all(self, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
        """List sessions, optionally filtered by their associated workspace."""
        sessions = []
        for p in self.storage_dir.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sws = data.get("workspace", "")
                if workspace is not None and not self.matches_workspace(sws, workspace):
                    continue
                sessions.append({
                    "id": data.get("id", p.stem),
                    "title": data.get("title", "Untitled"),
                    "model": data.get("model", "unknown"),
                    "mode": data.get("mode", "build"),
                    "updated_at": data.get("updated_at", 0),
                    "turns": len(data.get("messages", [])),
                    "workspace": sws,
                })
            except Exception:
                continue
        sessions.sort(key=lambda x: x["updated_at"], reverse=True)
        return sessions

    def fork(self, session_id: str, new_title: Optional[str] = None) -> Optional[Session]:
        """Fork an existing session into a new branched exploration."""
        orig = self.load(session_id)
        if not orig:
            return None
        new_sid = f"sess_fork_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        title = new_title or f"Fork of {orig.title}"
        forked = Session(
            id=new_sid,
            title=title,
            provider=orig.provider,
            model=orig.model,
            mode=orig.mode,
            permission=orig.permission,
            thinking_effort=orig.thinking_effort,
            messages=list(orig.messages),
            todos=list(orig.todos),
            total_tokens=orig.total_tokens,
            # Forks stay with the original session's workspace; falling back to
            # the manager's current workspace for pre-workspace sessions.
            workspace=orig.workspace or self._norm_workspace(self.workspace),
        )
        self.save(forked)
        return forked

    def delete(self, session_id: str) -> bool:
        file_path = self._file_path(session_id)
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def export_markdown(self, session_id: str, output_path: Path) -> bool:
        session = self.load(session_id)
        if not session:
            return False

        lines = [
            f"# Session Transcript: {session.title}",
            f"- **Session ID**: `{session.id}`",
            f"- **Provider / Model**: `{session.provider}` / `{session.model}`",
            f"- **Mode**: `{session.mode}` | **Permission**: `{session.permission}`",
            f"- **Date**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session.created_at))}",
        ]
        if session.workspace:
            lines.append(f"- **Workspace**: `{session.workspace}`")
        lines += [
            "",
            "---",
            "",
        ]

        for i, msg in enumerate(session.messages, 1):
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            lines.append(f"### Turn {i}: {role}")

            # Include reasoning content if present
            if msg.get("reasoning_content"):
                lines.append(f"\n> 💭 **Thinking Process**:\n> {msg['reasoning_content']}\n")

            if content:
                lines.append(content)

            if "tool_calls" in msg and msg["tool_calls"]:
                lines.append("\n**Tool Invocations:**")
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    name = fn.get("name", "tool")
                    args = fn.get("arguments", "{}")
                    lines.append(f"- `{name}` with arguments: `{args}`")

            lines.append("\n---\n")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return True
