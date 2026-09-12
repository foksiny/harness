---
name: test_architect
description: Automated test suite generation, unit and integration testing, edge-case analysis, and test-driven verification.
triggers: [test, pytest, unittest, jest, coverage, mock, tdd, regression]
---
# Test Architect for Harness

Expert discipline for authoring robust automated tests, uncovering regressions, and verifying edge cases.

## Testing Protocol
1. **Identify Critical Paths & Invariants**:
   - Happy path: expected standard inputs and outputs.
   - Boundary & edge conditions: 0, negative values, empty strings, None/null, maximum capacity.
   - Failure & error cases: network timeout, malformed payloads, invalid permissions.
2. **Isolation & Determinism**:
   - Avoid external network dependencies or unseeded random state.
   - Use mocks/stubs for external service boundaries.
3. **Execution**:
   - Execute tests using `run_command` with focused test selection (e.g. `pytest tests/test_auth.py -k test_token_expiry`).
   - Ensure clean exit codes and actionable failure messages.
