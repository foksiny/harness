# Project Rules for Harness Development

Welcome to the Harness codebase. When contributing or modifying this repository, follow these engineering standards:

## Prime Engineering Rules
1. **Lightweight & Fast**:
   - Keep startup time under 100ms.
   - Maintain RAM consumption below 35MB.
   - Avoid adding bloated dependencies. Rely on clean, modern Python 3.12 idioms.

2. **Multi-Provider & Dynamic Model Architecture**:
   - Never hardcode model names or static context windows. Always route through `detector.inspect_model()` so future models automatically resolve their context windows and thinking parameters.
   - Provider endpoints and streaming must conform to the unified `LLMChunk` and SSE parsing standard in `harness/providers/`. Each provider-native streaming event maps to exactly one `LLMChunk`, which also carries the decoded wire payload in `LLMChunk.native`; do not invent, split, or duplicate chunks. Consumers must preserve those boundaries: newline-less deltas concatenate on one line (interactive output holds a fragment until the provider's `\n` arrives, because prompt_toolkit's repaint cannot safely flush a partial line), and only an actual `\n` from the provider may end a streamed line — never synthesize an "auto newline" per chunk or partial paint.
   - **Provider complexity**: With 18 providers, each with different APIs, streaming formats, and payloads, consistency is critical. When modifying provider code, verify all affected providers still pass tests. Use the provider abstraction layer to minimize per-provider surface area.

3. **Safety & Permission Protocol**:
   - Never bypass the `PermissionManager` in file modification or shell execution tools.
   - Any new tool must declare its `action_type` and specify whether it is `is_read_only`.
   - Python code executed via `execute_python` must pass through the AST Safety Analyzer (`analyze_python_safety`).
   - `execute_python` runs in a layered sandbox (nsjail -> Docker -> restricted subprocess with rlimits); AST analysis (`analyze_python_safety`) is informational metadata on top, not the security boundary. Document both layers when modifying the tool.

4. **Testing Invariant**:
   - Every new feature, command, or tool must have accompanying unit tests in `tests/`.
   - Run `python3 -m unittest discover -s tests -v` to ensure 100% passing tests before committing changes.
   - **Bug surface awareness**: Each provider multiplies test cases. When adding a new provider or tool, add tests covering its specific API quirks, streaming behavior, and error paths.
