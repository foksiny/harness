"""
Skill discovery tools for Harness.
Lets the model inspect the skill catalog and load skill instructions on demand
instead of relying on skills being injected into the system prompt.
"""
from typing import Dict, Any, Optional
from harness.tools.base import Tool
from harness.skills.loader import SkillsManager

class ListSkillsTool(Tool):
    name = "list_skills"
    description = (
        "List the catalog of available skills (name, description, triggers, source). "
        "Call this at the start of a task, before doing any work, to see whether any "
        "skill applies to the user's use case."
    )
    action_type = "skill"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, skills_manager: SkillsManager):
        self.skills_manager = skills_manager

    def execute(self, **kwargs) -> str:
        skills = sorted(self.skills_manager.list_skills(), key=lambda s: s.name)
        if not skills:
            return "No skills currently registered."
        lines = [f"Available Skills ({len(skills)}):"]
        for s in skills:
            badge = "Built-in" if s.is_builtin else "Custom"
            line = f"- **{s.name}** [{badge}]: {s.description}"
            if s.triggers:
                line += f" | triggers: {', '.join(s.triggers)}"
            lines.append(line)
        lines.append("\nCall `read_skill` with a skill name to load its full instructions.")
        return "\n".join(lines)

class ReadSkillTool(Tool):
    name = "read_skill"
    description = (
        "Load the full instructions, trigger conditions, and workflow for a named skill. "
        "Call this after `list_skills` when a skill matches the user's use case, then "
        "follow the loaded instructions for the task."
    )
    action_type = "skill"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the skill to read (e.g. 'git_master')."},
        },
        "required": ["name"],
    }

    def __init__(self, skills_manager: SkillsManager):
        self.skills_manager = skills_manager

    def execute(self, name: str, **kwargs) -> str:
        skill = self._lookup(name)
        if not skill:
            available = ", ".join(sorted(s.name for s in self.skills_manager.list_skills()))
            return f"Error: Skill '{name}' not found. Available skills: {available or 'none'}"
        triggers = ", ".join(skill.triggers) if skill.triggers else "n/a"
        source = skill.source_path or "unknown"
        return (
            f"=== SKILL: {skill.name} ===\n"
            f"Description: {skill.description}\n"
            f"Triggers: {triggers}\n"
            f"Source: {source}\n"
            f"--- INSTRUCTIONS ---\n"
            f"{skill.instructions}"
        )

    def _lookup(self, name: str) -> Optional[Any]:
        key = name.strip().lower()
        for skill in self.skills_manager.list_skills():
            if skill.name.lower() == key:
                return skill
        return None