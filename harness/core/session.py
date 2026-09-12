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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        return cls(**data)

class SessionManager:
    """Manages active and historical sessions."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = Path(storage_dir) if storage_dir else (Path.home() / ".harness" / "sessions")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, session_id: str) -> Path:
        clean_id = session_id.replace("/", "_").replace("\\", "_")
        return self.storage_dir / f"{clean_id}.json"

    def create(self, provider: str, model: str, mode: str, permission: str, thinking_effort: str, title: str = "New Session") -> Session:
        sid = f"sess_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        session = Session(
            id=sid,
            title=title,
            provider=provider,
            model=model,
            mode=mode,
            permission=permission,
            thinking_effort=thinking_effort,
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

    def list_all(self) -> List[Dict[str, Any]]:
        sessions = []
        for p in self.storage_dir.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    sessions.append({
                        "id": data.get("id", p.stem),
                        "title": data.get("title", "Untitled"),
                        "model": data.get("model", "unknown"),
                        "mode": data.get("mode", "build"),
                        "updated_at": data.get("updated_at", 0),
                        "turns": len(data.get("messages", [])),
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
