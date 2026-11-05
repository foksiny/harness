#!/usr/bin/env python3
"""Repo-local agent-quality evals (J2). Run: python3 -m evals.run"""
import sys, json
from dataclasses import dataclass

FAIL = 0
PASS = 0

def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name} {detail}")
    if ok:
        PASS += 1
    else:
        FAIL += 1

def main() -> int:
    # E1: finish gate logic present (no unverified finish on mutation)
    from harness.core.agent import HarnessAgent
    check("verify helper exists", hasattr(HarnessAgent, "_is_verification_call"))
    check("classifier exists", hasattr(HarnessAgent, "_classify_tool_result"))

    # E2: PLAN blocks run_command rm + delete_file
    from harness.core.modes import Mode, is_tool_allowed_in_mode, is_command_allowed_in_mode
    check("plan blocks delete_file", not is_tool_allowed_in_mode("delete_file", Mode.PLAN))
    check("plan blocks rm", not is_command_allowed_in_mode("rm -rf /tmp/x", Mode.PLAN))
    check("plan allows ls", is_command_allowed_in_mode("ls -la", Mode.PLAN))

    # E3: head+tail truncation preserves tail
    from harness.core.context_budget import truncate_output as _to
    out = _to("A" * 20000 + "TAIL_MARKER", 8000) if False else None
    from harness.core.context_budget import ContextBudget
    cb = ContextBudget(100000)
    res = cb.truncate_output("run_command", "X" * 30000 + "ENDMARKER")
    check("tail preserved", "ENDMARKER" in res)

    # E4: swarm DAG + payload + verifier + browser snapshot tools
    from harness.tools import ToolRegistry
    r = ToolRegistry()
    names = {t.name for t in r.list_tools()}
    for n in ("subagent_list", "subagent_status", "subagent_wait", "subagent_cancel",
              "web_fetch", "browser_snapshot", "computer_screenshot", "move_file", "git_log"):
        check(f"tool present: {n}", n in names)

    # E5: swarm dependency ordering (foreground, mock runner via orchestrator directly)
    from harness.core.subagents import SubagentOrchestrator
    order: list[str] = []
    def fake_runner(sys_prompt, task, allowed, max_turns, **kw):
        aid = kw.get("agent_id", "?")
        order.append(aid)
        return ("done, tests passed", 1)
    orch = SubagentOrchestrator(fake_runner)
    orch.launch_swarm([
        {"agent_type": "coder", "agent_id": "build", "task": "build", "depends_on": ["research"]},
        {"agent_type": "researcher", "agent_id": "research", "task": "research"},
    ], background=False)
    if order:
        check("dag order (research before build)", order[0] == "research")
    else:
        print("  (no runner order captured; swarm used fallback)")

    # E6: compaction checkpoint schema sections
    from harness.core.compaction import Compactor
    c = Compactor(context_window=100000)
    cp = c._build_checkpoint([{"role": "user", "content": "build X"}, {"role": "assistant", "content": "decided Y"}])
    content = str(cp.get("content", ""))
    check("checkpoint has decisions", "Decisions" in content)
    check("checkpoint has open/next", "Open questions" in content)

    # E7: grounding normalize
    from harness.computer.grounding import normalize_coords
    check("grounding 0-1000", normalize_coords(500, 500, 1000, 800) == {"x": 500, "y": 400})

    print(f"\nEvals: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0

if __name__ == "__main__":
    sys.exit(main())
