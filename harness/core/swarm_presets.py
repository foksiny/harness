"""Topology presets for swarms (B7). Each preset = DAG + roles."""
from typing import Dict, Any, List

PRESETS: Dict[str, Dict[str, Any]] = {
    "research-build-verify": {
        "description": "Waves: researchers → coders (depends_on researcher) → tester/reviewer.",
        "agents": [
            {"agent_type": "researcher", "agent_id": "research", "task": "Research the task area: files, conventions, APIs. Cite file refs.", "depends_on": [], "paths": []},
            {"agent_type": "coder", "agent_id": "build", "task": "Implement per the research findings. Own only the files you create/edit. Run tests; report evidence.", "depends_on": ["research"], "paths": []},
            {"agent_type": "tester", "agent_id": "verify", "task": "Write/run tests for the built work. Report exact commands + pass/fail. If it fails, say so.", "depends_on": ["build"], "paths": []},
        ],
    },
    "parallel-coders": {
        "description": "Independent coders on disjoint files + integrator reminder.",
        "agents": [
            {"agent_type": "coder", "agent_id": "coder_a", "task": "Implement module A (own only its files). Run its tests; report evidence.", "depends_on": [], "paths": []},
            {"agent_type": "coder", "agent_id": "coder_b", "task": "Implement module B (own only its files). Run its tests; report evidence.", "depends_on": [], "paths": []},
            {"agent_type": "reviewer", "agent_id": "integrate_check", "task": "Review interfaces between modules A and B for integration risks. Verdict pass/fail.", "depends_on": ["coder_a", "coder_b"], "paths": []},
        ],
    },
    "debate": {
        "description": "Two solvers + judge (best-of-n selection pattern).",
        "agents": [
            {"agent_type": "coder", "agent_id": "solver_1", "task": "Solve the task. Explain your approach, implement, run tests, report evidence.", "depends_on": [], "paths": []},
            {"agent_type": "coder", "agent_id": "solver_2", "task": "Solve the task differently from the obvious approach. Implement, run tests, report evidence.", "depends_on": [], "paths": []},
            {"agent_type": "reviewer", "agent_id": "judge", "task": "Compare both solutions: which is correct, tested, and cleaner? Return verdict with file refs.", "depends_on": ["solver_1", "solver_2"], "paths": []},
        ],
    },
}

def get_preset(name: str) -> Dict[str, Any] | None:
    return PRESETS.get((name or "").lower().strip())

def list_presets() -> List[str]:
    return list(PRESETS.keys())
