# Project Rules for Harness Development

Welcome to the Harness codebase. When contributing or modifying this repository, follow these engineering standards:

## Prime Engineering Rules
1. **Lightweight & Fast**:
   - Keep startup time under 100ms.
   - Maintain RAM consumption below 35MB.
   - Avoid adding bloated dependencies. Rely on clean, modern Python 3.12 idioms.

2. **Multi-Provider & Dynamic Model Architecture**:
   - Never hardcode model names or static context windows. Always route through `detector.inspect_model()` so future models automatically resolve their context windows and thinking parameters.
   - Provider endpoints and streaming must conform to the unified `LLMChunk` and SSE parsing standard in `harness/providers/`.

3. **Safety & Permission Protocol**:
   - Never bypass the `PermissionManager` in file modification or shell execution tools.
   - Any new tool must declare its `action_type` and specify whether it is `is_read_only`.
   - Python code executed via `execute_python` must pass through the AST Safety Analyzer (`analyze_python_safety`).

4. **Testing Invariant**:
   - Every new feature, command, or tool must have accompanying unit tests in `tests/`.
   - Run `python3 -m unittest discover -s tests -v` to ensure 100% passing tests before committing changes.
