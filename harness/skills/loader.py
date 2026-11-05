"""
Skills Loader & Registry for Harness.
Discovers, parses, and injects contextual skills from builtin, global, and workspace sources.
"""
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

@dataclass
class Skill:
    name: str
    description: str
    triggers: List[str] = field(default_factory=list)
    source_path: Optional[str] = None
    is_builtin: bool = False
    allowed_tools: List[str] = field(default_factory=list)  # H10 capability manifest (empty = unrestricted)
    trusted: bool = True  # H10: workspace skills need approval before first use
    # Instruction bodies are large (the built-in motion catalog alone holds ~560KB),
    # so they are read on first access instead of at discovery time. Use load_instructions().
    _instructions: Optional[str] = field(default=None, repr=False, compare=False)

    def load_instructions(self) -> str:
        """Return the skill body, reading it from disk on first access."""
        if self._instructions is None:
            try:
                if not self.source_path:
                    self._instructions = ""
                    return self._instructions
                with open(self.source_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except (OSError, TypeError):
                self._instructions = ""
                return self._instructions
            m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
            self._instructions = (m.group(2) if m else content).strip()
        return self._instructions

    @property
    def instructions(self) -> str:
        return self.load_instructions()

    def matches(self, query: str) -> bool:
        """Check if query or task triggers this skill."""
        q = query.lower()
        if self.name.lower() in q or self.name.replace("_", " ").lower() in q:
            return True
        for trig in self.triggers:
            t = trig.lower().strip()
            if t in q:
                return True
            words = t.split()
            if len(words) > 1 and all(w in q for w in words):
                return True
        return False

def parse_skill_markdown(content: str, source_path: Optional[str] = None, is_builtin: bool = False,
                         allow_bare_markdown: bool = False) -> Optional[Skill]:
    """Parse a SKILL.md file with YAML frontmatter.

    Returns None when the file is not a skill definition. A markdown document with no
    frontmatter is only accepted when allow_bare_markdown is set, which the manager does
    for user skill dirs (~/.harness/skills, .harness/skills) where a bare `my_skill.md` is
    a legitimate way to define one. The builtin dir ships non-skill docs (license and
    provenance records) alongside its skills, so those must carry frontmatter.

    Only the frontmatter is parsed; the instruction body is discarded and re-read
    lazily by Skill.load_instructions() (see the _instructions field).
    """
    meta = {}
    instructions = content

    # Check for frontmatter
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if fm_match:
        frontmatter, instructions = fm_match.groups()
        for line in frontmatter.split("\n"):
            line = line.strip()
            if ":" in line:
                key, val = line.split(":", 1)
                k = key.strip().lower()
                v = val.strip()
                if v.startswith("[") and v.endswith("]"):
                    # Parse simple list [a, b, c]
                    items = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
                    meta[k] = items
                else:
                    meta[k] = v.strip("'\"")

    if not meta and not allow_bare_markdown:
        # A loose markdown file with no frontmatter is documentation, not a skill
        # (e.g. the attribution record in the builtin dir).
        return None

    name = meta.get("name")
    if not name and source_path:
        name = Path(source_path).parent.name if Path(source_path).name == "SKILL.md" else Path(source_path).stem

    if not name:
        return None

    desc = meta.get("description", "No description provided.")
    triggers = meta.get("triggers", [])
    if isinstance(triggers, str):
        triggers = [triggers]
    allowed = meta.get("allowed_tools", [])
    if isinstance(allowed, str):
        allowed = [allowed]
    # Trusted only when builtin or explicitly approved; workspace skills start untrusted.
    trusted = bool(is_builtin or (meta.get("trusted") or "").lower() in ("true", "yes", "1"))

    return Skill(
        name=name,
        description=desc,
        triggers=triggers,
        allowed_tools=allowed,
        trusted=trusted,
        # With a source_path the body is re-read on demand; without one the only
        # available body is the content we just parsed.
        _instructions=None if source_path else instructions.strip(),
        source_path=str(source_path) if source_path else None,
        is_builtin=is_builtin,
    )

class SkillsManager:
    """Manages skill discovery, loading, and prompt integration."""

    def __init__(self, workspace_path: Optional[Path] = None):
        self.workspace_path = workspace_path or Path.cwd()
        self.skills: Dict[str, Skill] = {}
        self.builtin_dir = Path(__file__).parent / "builtin"
        self.global_dir = Path.home() / ".harness" / "skills"
        self.workspace_dir = self.workspace_path / ".harness" / "skills"
        self.reload()

    def reload(self) -> None:
        """Scan all skill directories and populate registry."""
        self.skills.clear()

        # 1. Builtin skills
        self._scan_directory(self.builtin_dir, is_builtin=True)

        # 2. Global user skills (a bare <name>.md with no frontmatter is valid here)
        if self.global_dir.exists():
            self._scan_directory(self.global_dir, is_builtin=False, allow_bare_markdown=True)

        # 3. Workspace skills (highest override priority)
        if self.workspace_dir.exists():
            self._scan_directory(self.workspace_dir, is_builtin=False, allow_bare_markdown=True)

    def _scan_directory(self, dir_path: Path, is_builtin: bool = False,
                        allow_bare_markdown: bool = False) -> None:
        if not dir_path.exists():
            return

        # Check subfolders for SKILL.md
        for item in dir_path.iterdir():
            if item.is_dir():
                skill_file = item / "SKILL.md"
                if skill_file.exists():
                    try:
                        with open(skill_file, "r", encoding="utf-8") as f:
                            skill = parse_skill_markdown(f.read(), str(skill_file), is_builtin)
                            if skill:
                                self.skills[skill.name] = skill
                    except Exception:
                        pass
            elif item.is_file() and item.suffix == ".md":
                try:
                    with open(item, "r", encoding="utf-8") as f:
                        skill = parse_skill_markdown(f.read(), str(item), is_builtin,
                                                      allow_bare_markdown)
                        if skill:
                            self.skills[skill.name] = skill
                except Exception:
                    pass

    def get(self, name: str) -> Optional[Skill]:
        return self.skills.get(name)

    def list_skills(self) -> List[Skill]:
        return list(self.skills.values())

    def match_skills(self, query: str) -> List[Skill]:
        """Find skills triggered by user prompt."""
        matched = []
        for s in self.skills.values():
            if s.matches(query):
                matched.append(s)
        return matched

    def format_summary(self) -> str:
        """Format index of available skills for system prompt."""
        if not self.skills:
            return "No skills currently registered."
        lines = ["Available Skills (Call upon or reference when relevant):"]
        for s in sorted(self.skills.values(), key=lambda x: x.name):
            badge = "[Built-in]" if s.is_builtin else "[Custom]"
            lines.append(f"- **{s.name}** {badge}: {s.description}")
        return "\n".join(lines)
