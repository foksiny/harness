"""
Input Handler for Harness TUI.
Supports autocompletion, command history, and graceful prompt_toolkit / readline fallback.
"""
from typing import List, Optional

SLASH_COMMANDS = [
    "/help", "/btw", "/steer", "/goal", "/mode", "/perm", "/theme",
    "/provider", "/model", "/effort", "/todo", "/skills", "/mcp",
    "/subagent", "/compact", "/session", "/tokens", "/diff", "/clear",
    "/exit", "/quit"
]

class InputHandler:
    """Provides interactive prompt with auto-completion and command history."""

    def __init__(self):
        self._has_prompt_toolkit = False
        self._pt_session = None

        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.history import InMemoryHistory

            completer = WordCompleter(SLASH_COMMANDS, sentence=True)
            self._pt_session = PromptSession(completer=completer, history=InMemoryHistory())
            self._has_prompt_toolkit = True
        except ImportError:
            # Setup standard readline autocompletion
            try:
                import readline
                def complete(text, state):
                    matches = [c for c in SLASH_COMMANDS if c.startswith(text)]
                    return matches[state] if state < len(matches) else None

                readline.set_completer(complete)
                readline.parse_and_bind("tab: complete")
            except Exception:
                pass

    def get_input(self, prompt_text: str = "Harness> ") -> str:
        """Read a line of input from user."""
        if self._has_prompt_toolkit and self._pt_session:
            try:
                return self._pt_session.prompt(prompt_text).strip()
            except (EOFError, KeyboardInterrupt):
                return "/exit"

        # Standard fallback
        try:
            return input(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            return "/exit"
