"""Ultra-goal phase machine as code (G6). Phases must complete with evidence."""
from enum import Enum


class UltraPhase(str, Enum):
    DISCOVER = "discover"
    PLAN = "plan"
    INTERVIEW = "interview"
    GENERATE = "generate"
    INTEGRATE = "integrate"
    VERIFY = "verify"
    REVIEW = "review"
    DONE = "done"


ORDER = [UltraPhase.DISCOVER, UltraPhase.PLAN, UltraPhase.INTERVIEW,
         UltraPhase.GENERATE, UltraPhase.INTEGRATE, UltraPhase.VERIFY,
         UltraPhase.REVIEW, UltraPhase.DONE]


class UltraPhaseTracker:
    """Tracks phases for one ultra-goal turn. Entry checks are evidence-based."""

    def __init__(self):
        self.phases = {p: {"done": False, "evidence": ""} for p in ORDER}
        self.current = UltraPhase.DISCOVER

    def advance(self, phase: UltraPhase, evidence: str = "") -> bool:
        if phase not in self.phases:
            return False
        self.phases[phase]["done"] = True
        self.phases[phase]["evidence"] = evidence or ""
        idx = ORDER.index(phase)
        if idx + 1 < len(ORDER):
            self.current = ORDER[idx + 1]
        return True

    def can_finish(self) -> bool:
        return all(self.phases[p]["done"] for p in ORDER[:-1])

    def summary(self) -> str:
        return ", ".join(f"{p.value}:{'done' if self.phases[p]['done'] else 'todo'}" for p in ORDER)
