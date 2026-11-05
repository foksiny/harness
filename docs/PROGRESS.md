# Implementation Progress — IMPROVEMENT_PLAN.md

Goal: implement everything in docs/IMPROVEMENT_PLAN.md (P0→P2; P3 where feasible without violating lightweight constraint).

## Steps
1. P0 core gates: finish contract, todo gate, verify gate, prompt Directive#9, modes PLAN, context head+tail+ceiling, config budgets/temp. Check: pytest test_finish/test_context_budget/test_modes/test_ultra — DONE (93 passed)
2. Delegation/swarm lifecycle: subagents A1-A12, B1-B7 (run ids, cancel/timeout/caps, DAG, handoff, verifier, presets), todo graph. Check: pytest test_swarm/test_subagents — DONE (24 passed)
3. Computer+web: computer tools, browser snapshot, web_fetch, search synthesis, grounding. Check: pytest test_browser/test_computer/test_web_fetch — DONE
4. Security/tools/MCP: PLAN shell, perms routing, run_command confine, sandbox fail-closed, MCP lazy, skill trust, missing tools. Check: pytest test_tools/test_mcp/test_modes — DONE
5. Context/learning/evals: compaction schema, learning match, evals suite, bench gate, telemetry. Check: full suite — DONE (707 passed)
6. Final: full test suite + bench, update plan status, critique round. — DONE

## Evidence
- Full suite: 707 passed (was 694), 1 warning
- Evals: 19 passed (`python3 -m evals.run`)
- Bench: cold 95ms, RSS 33MB, 65 tools (was 46)
- New tests: tests/test_computer.py, tests/test_web_fetch.py, tests/test_evals_gate.py
- New modules: harness/computer/grounding.py, harness/tools/computer.py, harness/core/swarm_presets.py, harness/core/ultra_phases.py, harness/core/turn_executor.py, evals/run.py

## Critique round
- Missing? P3 heavy items (VLM grounding, remote search API, cross-instance mesh) are documented stubs/opt-ins — gated on evals per plan.
- Assumptions checked: finish gate allows 2 rejections so trivial turns still finish; PLAN checks command words; computer tools degrade gracefully headless.
- Simpler path? Gates piggyback existing transcript inspection (no extra model calls) — cheapest mechanism.

## Notes on deliberate deviations
- Search synthesis: structured chain kept; full rerank/synthesis pass deferred to eval-gated P3 (free-chain parsers pinned by fixture tests in tests/test_web_fetch.py pattern).
- Sandbox: restricted backend kept behind test/opt-in (config sandbox_backend) rather than hard fail-closed for local dev, but now uses temp HOME/cwd and never silent.
- Mesh stubs: hidden from registry/schemas (H9) rather than deleted — backward-compat import path preserved.
