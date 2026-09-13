"""
Agent Self-Improvement & Learning Memory for Harness.
Persists reusable "lessons" learned across sessions (workspace override + global
cascade, mirroring skills/config), injects top matches into the system prompt,
and can promote a matured lesson into a real SKILL.md file.
"""
import json
import threading
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

MAX_LESSONS = 200
PROMOTE_HIT_THRESHOLD = 3


def _lesson_id() -> str:
    return f"lsn_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


@dataclass
class Lesson:
    id: str
    summary: str
    tags: List[str] = field(default_factory=list)
    evidence: str = ""
    source_session: str = ""
    created_at: float = field(default_factory=time.time)
    hits: int = 0
    last_used: float = field(default_factory=time.time)
    promoted: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "Lesson":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in valid})


class LearningManager:
    """Stores, matches, injects, and promotes learned lessons.

    Storage cascades like skills/config: workspace ``.harness/memories.json``
    overrides global ``~/.harness/memories.json``. Everything is lazy-loaded on
    first access so construction stays trivially cheap (startup <100ms).
    """

    def __init__(
        self,
        workspace_path: Optional[Path] = None,
        storage_dir: Optional[Path] = None,
    ):
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.workspace_file = self.workspace_path / ".harness" / "memories.json"
        self.global_file = (Path(storage_dir) if storage_dir else Path.home() / ".harness") / "memories.json"
        self._workspace: Dict[str, Lesson] = {}
        self._global: Dict[str, Lesson] = {}
        self._loaded = False
        self._lock = threading.Lock()
        self._reload_hook = None

    def set_reload_hook(self, hook) -> None:
        """Register a callable invoked after a skill promotion (e.g. SkillsManager.reload)."""
        self._reload_hook = hook

    def reload_skills(self) -> None:
        """Re-scan the workspace skill directories so promoted lessons are active immediately."""
        if self._reload_hook:
            self._reload_hook()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._global = self._load_file(self.global_file)
        self._workspace = self._load_file(self.workspace_file)
        self._loaded = True

    @staticmethod
    def _load_file(file_path: Path) -> Dict[str, Lesson]:
        lessons: Dict[str, Lesson] = {}
        try:
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data.get("lessons", []):
                    lesson = Lesson.from_dict(item)
                    lessons[lesson.id] = lesson
        except Exception:
            pass
        return lessons

    def _persist(self) -> None:
        for file_path, lessons in ((self.global_file, self._global), (self.workspace_file, self._workspace)):
            if not lessons:
                continue
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump({"lessons": [l.to_dict() for l in lessons.values()]}, f, indent=2)
            except Exception:
                pass

    def _all_lessons(self) -> List[Lesson]:
        merged = dict(self._global)
        merged.update(self._workspace)
        return list(merged.values())

    def record(
        self,
        summary: str,
        tags: Optional[List[str]] = None,
        evidence: str = "",
        source_session: str = "",
    ) -> str:
        """Record a new lesson and persist it to the workspace store."""
        with self._lock:
            self._ensure_loaded()
            lesson = Lesson(
                id=_lesson_id(),
                summary=(summary or "").strip(),
                tags=[str(t).strip().lower() for t in (tags or []) if str(t).strip()],
                evidence=(evidence or "").strip(),
                source_session=source_session or "",
            )
            if not lesson.summary:
                return ""
            self._workspace[lesson.id] = lesson
            self._enforce_cap()
            self._persist()
            return lesson.id

    def list(self) -> List[Lesson]:
        """Return all lessons (global + workspace, workspace override priority)."""
        self._ensure_loaded()
        return self._all_lessons()

    def forget(self, lesson_id: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            removed = self._workspace.pop(lesson_id, None) or self._global.pop(lesson_id, None)
            if removed:
                self._persist()
            return removed is not None

    def get(self, lesson_id: str) -> Optional[Lesson]:
        self._ensure_loaded()
        return self._workspace.get(lesson_id) or self._global.get(lesson_id)

    def hit(self, lesson_id: str) -> None:
        with self._lock:
            self._ensure_loaded()
            lesson = self._workspace.get(lesson_id) or self._global.get(lesson_id)
            if not lesson:
                return
            lesson.hits += 1
            lesson.last_used = time.time()
            if lesson.hits >= PROMOTE_HIT_THRESHOLD and not lesson.promoted:
                self.promote(lesson_id)
            elif lesson_id in self._workspace:
                self._persist()

    def match(self, query: str, k: int = 3) -> List[Lesson]:
        """Return top-k lessons whose summary/tags match the query keywords."""
        self._ensure_loaded()
        q = (query or "").lower()
        tokens = [t for t in q.replace("_", " ").split() if len(t) > 2]
        scored = []
        for lesson in self._all_lessons():
            summary = lesson.summary.lower()
            tags = " ".join(lesson.tags).lower()
            score = 0
            if lesson.summary and any(t in summary for t in tokens):
                score += 2
            if any(t in tags for t in tokens):
                score += 1
            if lesson.summary.lower() in q:
                score += 3
            if score > 0:
                scored.append((score, lesson))
        scored.sort(key=lambda x: (x[0], x[1].hits), reverse=True)
        return [lesson for _, lesson in scored[:k]]

    def format_top(self, query: str = "", max_chars: int = 600) -> str:
        lessons = self.match(query, k=3)
        if not lessons:
            return ""
        lines = ["Here is a distilled memory of prior lessons relevant to your current task (from this workspace and your global history):"]
        total = 0
        for lesson in lessons:
            entry = f"- {lesson.summary}"
            if lesson.tags:
                entry += f" [tags: {', '.join(lesson.tags)}]"
            if lesson.evidence:
                entry += f" (evidence: {lesson.evidence[:120]})"
            entry = entry[:300]
            if total + len(entry) + 1 > max_chars:
                break
            lines.append(entry)
            total += len(entry) + 1
            self.hit(lesson.id)
        return "\n".join(lines)

    def promote(self, lesson_id: str, name: Optional[str] = None) -> bool:
        """Promote a lesson into a real workspace skill (SKILL.md + reload).

        Returns False if the lesson is missing or promotion failed.
        """
        lesson = self.get(lesson_id)
        if not lesson:
            return False
        slug = (name or lesson.summary or "learned_lesson").strip().lower()
        slug = "".join(c if c.isalnum() or c in "_" else "_" for c in slug).strip("_")[:60]
        slug = slug.replace(" ", "_") or "learned_lesson"

        skill_dir = self.workspace_path / ".harness" / "skills" / slug
        skill_file = skill_dir / "SKILL.md"
        try:
            skill_file.parent.mkdir(parents=True, exist_ok=True)
            trigger = lesson.tags[0] if lesson.tags else (
                next((t for t in lesson.summary.lower().split() if len(t) > 3), "learned_lesson")
            )
            content = f"""---
name: {slug}
description: {lesson.summary}
triggers: [learned {trigger}]
---
# {slug.replace('_', ' ').title()}

{lesson.summary}

## Guidance
- Apply this lesson when the described situation arises.
- Verify the relevant commands or files before acting.
- Keep the change minimal and atomic.
"""
            with open(skill_file, "w", encoding="utf-8") as f:
                f.write(content)
            lesson.promoted = True
            self._persist()
            if self._reload_hook:
                self._reload_hook()
            return True
        except Exception:
            return False

    def _enforce_cap(self) -> None:
        lessons = self._all_lessons()
        if len(lessons) <= MAX_LESSONS:
            return
        excess = len(lessons) - MAX_LESSONS
        victims = sorted(lessons, key=lambda l: (l.last_used, l.created_at))[:excess]
        for victim in victims:
            self._workspace.pop(victim.id, None)
            self._global.pop(victim.id, None)

    def stats(self) -> Dict[str, int]:
        self._ensure_loaded()
        return {
            "global": len(self._global),
            "workspace": len(self._workspace),
            "total": len(self._all_lessons()),
            "promoted": sum(1 for l in self._all_lessons() if l.promoted),
        }