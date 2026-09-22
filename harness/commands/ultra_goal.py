"""
ULTRA-GOAL engine for Harness.
Powers the /ultra-goal command: a maximum-capability autonomous full product /
app / game build. The command launches a Super Mode mission; the AGENT itself
interviews the user (via ask_user) so it knows exactly what is wanted before
writing any files, then uses parallel subagent/squad file generation,
self-owned integration, and enforced verification of real, runnable artifacts.
"""
from typing import Dict, Optional

from harness.core.prompt import ULTRA_GOAL_MARKER


def build_ultra_goal_brief(goal: str) -> str:
    """Compose the ULTRA-GOAL mission prompt delivered to the agent.

    The ``ULTRA_GOAL_MARKER`` prefix switches the model into ULTRA-GOAL
    protocol (see harness.core.prompt.ULTRA_GOAL_PROTOCOL) for the whole turn.
    Requirement-gathering is deliberately NOT done here: the agent interviews
    the user itself (mandated in the protocol's phase 2) so it adapts to the
    actual answers instead of a rigid pre-flight form.
    """
    return "\n".join([
        f"{ULTRA_GOAL_MARKER} {goal.strip()}",
        "",
        "## MISSION:",
        "Execute the ULTRA-GOAL PROTOCOL from your system prompt to completion. Concretely:",
        "1) **INTERVIEW** the user with `ask_user` first (what kind of app/game, tech stack,",
        "   platform, must-have features, scope, where it should live) until you know exactly",
        "   what is wanted — do not write files before that. Ask only what is not already clear.",
        "2) **PLAN** the architecture and the full file map; create todo_create milestones and a checkpoint.",
        "3) **GENERATE** with `spawn_swarm`: dispatch parallel `coder` agents owning distinct",
        "   files (no shared-file races), then integrate the pieces yourself so everything connects.",
        "4) **VERIFY**: install dependencies, run tests/builds, and actually LAUNCH the artifact;",
        "   reproduce → diagnose → patch → re-run until it works.",
        "5) **DELIVER**: finish with the file tree, how to run it, verification evidence, and next steps.",
        "",
        "Build a REAL, working thing. Anything stubbed out must be implemented before you finish.",
    ])


def print_ultra_goal_banner(renderer, goal: str) -> None:
    """Render the ULTRA-GOAL launch banner (mirrors the super banner)."""
    try:
        from rich.panel import Panel
        body = [
            f"🎯 [bold bright_magenta]ULTRA-GOAL ACTIVATED[/bold bright_magenta]",
            f"[bold white]Mission:[/bold white] {goal.strip()}",
            "",
            "[dim]The agent will interview you with `ask_user` before it writes any files —",
            "answer its questions so the build matches exactly what you want.[/dim]",
            "",
            "⚡ [bold]Phase machine:[/bold] interview → plan → swarm-generate files in parallel → integrate → build/run/tests → iterate until verified.",
        ]
        renderer.console.print(Panel("\n".join(body), title="⚡ ULTRA SUPER AGENT LOOP", border_style="bright_magenta"))
    except Exception:
        renderer.print_super_banner(goal)