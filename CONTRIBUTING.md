# Contributing to Harness

Thanks for your interest in contributing to Harness. This document covers the development workflow, testing expectations, and architecture conventions.

## Getting Started

```bash
git clone https://github.com/foksiny/harness.git
cd harness
pip install -r requirements.txt
python3 -m unittest discover -s tests -v
```

All tests must pass before opening a PR. Run:
```bash
python3 -m pytest tests/ -q
```
The total test count is maintained in `pyproject.toml` under `[tool.pytest.ini_options]`.

## Development Workflow

1. Fork and branch from `main`
2. Write tests first — all code must be hermetic (no real displays, no real APIs in tests)
3. Run `python3 -m unittest discover -s tests -q` — must be green
4. Keep commits focused: one logical change per commit
5. Open a PR with a clear description of what changed and why

## Testing Conventions

- **Hermetic tests**: never touch real screens, displays, or external APIs
- **ToolRegistry seam injection**: use the `computer_controller_factory` and `vision_describe` constructor params for testing computer tools without a real controller
- **Bare ToolRegistry** = zero computer tools (verifies seam isolation)
- **Run benchmarks**: `python3 benchmarks/bench_startup.py` before/after performance-sensitive changes

## Architecture Principles

- **Hermetic-first**: all computer tools use injectable seams; tests never touch real displays
- **Provider-agnostic**: works with Anthropic, OpenAI, Google, DeepSeek, local Ollama, and any OpenAI-compatible endpoint
- **Mode-gated**: PLAN mode blocks execution tools, SECURE mode requires approval, FULL mode is unrestricted
- **Skill-discoverable**: custom skills from `~/.harness/skills/` or `.harness/skills/` are auto-discovered

## Code Style

- Python 3.10+
- No external dependencies beyond `requests` (already required)
- Follow existing naming conventions in the file you're editing
- Keep imports at module level; use constructor injection for testability

## Reporting Issues

Use the [issue tracker](https://github.com/foksiny/harness/issues). Include:
- Harness version (`python3 -c "import harness; print(harness.__version__)"`)
- Python version
- Provider and model being used
- Minimal reproduction steps

## License

MIT — see [LICENSE](LICENSE).
