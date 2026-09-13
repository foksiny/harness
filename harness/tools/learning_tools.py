"""
Learning & self-improvement tools for Harness.
Lets the model deliberately record what it learned, recall prior lessons mid-task,
and promote a matured memory into a real reusable skill.
"""
from typing import Optional, List
from harness.tools.base import Tool
from harness.core.learning import LearningManager


class LearnRecordTool(Tool):
    name = "learn_record"
    description = (
        "Record a concise, reusable engineering lesson you learned this turn (a pitfall, "
        "a convention, a fix, a useful command sequence). Call this when you discover "
        "something the next session in this workspace should know: it is persisted across "
        "sessions and automatically injected into future system prompts when relevant."
    )
    action_type = "learning"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "One-sentence lesson, e.g. 'Tests in this repo run with `python3 -m unittest discover -s tests -q`.'",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional keywords used to match this lesson to future tasks (e.g. ['testing', 'python']).",
            },
        },
        "required": ["summary"],
    }

    def __init__(self, learning_manager: LearningManager):
        self.learning_manager = learning_manager

    def execute(self, summary: str, tags: Optional[List[str]] = None, **kwargs) -> str:
        lesson_id = self.learning_manager.record(summary=summary, tags=tags or [])
        if not lesson_id:
            return "Error: learn_record requires a non-empty summary."
        return f"Recorded lesson `{lesson_id}`: {summary}"


class LearnRecallTool(Tool):
    name = "learn_recall"
    description = (
        "Recall memories/lessons learned in previous sessions of this workspace (and globally). "
        "Use this when starting work that may repeat past knowledge, or when you suspect a "
        "related lesson exists. Returns the top matching lessons plus their tags."
    )
    action_type = "learning"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What you are working on now; matching lessons will be returned (e.g. 'run python tests').",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum lessons to return (default 3, max 5).",
            },
        },
        "required": ["query"],
    }

    def __init__(self, learning_manager: LearningManager):
        self.learning_manager = learning_manager

    def execute(self, query: str, limit: int = 3, **kwargs) -> str:
        lessons = self.learning_manager.match(query, k=max(1, min(int(limit or 3), 5)))
        if not lessons:
            return "No prior lessons match this query."
        lines = [f"Prior lessons relevant to \"{query}\" ({len(lessons)}):"]
        for lesson in lessons:
            tags = f" [tags: {', '.join(lesson.tags)}]" if lesson.tags else ""
            promoted = " (promoted to skill)" if lesson.promoted else ""
            lines.append(f"- `{lesson.id}`{tags}{promoted}: {lesson.summary}")
        return "\n".join(lines)


class LearnPromoteTool(Tool):
    name = "learn_promote"
    description = (
        "Promote a recorded lesson into a real reusable skill (writes a SKILL.md under "
        ".harness/skills/ and reloads the skill catalog, so it becomes a first-class skill "
        "the agent can read_skill). Best used for lessons proven valuable across multiple tasks."
    )
    action_type = "learning"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "lesson_id": {
                "type": "string",
                "description": "Lesson id as returned by learn_record / learn_recall.",
            },
            "name": {
                "type": "string",
                "description": "Optional skill name (lowercase, underscores). Defaults to a slug of the summary.",
            },
        },
        "required": ["lesson_id"],
    }

    def __init__(self, learning_manager: LearningManager):
        self.learning_manager = learning_manager

    def execute(self, lesson_id: str, name: Optional[str] = None, **kwargs) -> str:
        if self.learning_manager.promote(lesson_id, name=name):
            return f"Promoted lesson `{lesson_id}` into a skill (catalog reloaded)."
        return f"Error: lesson `{lesson_id}` not found or could not be promoted."