"""
Input Handler for Harness TUI.
Supports autocompletion, command history, view keybinds (F2 opens the agents
board, ESC returns to the parent view), and graceful prompt_toolkit / readline fallback.
When prompt_toolkit is not installed the fallback uses a raw terminal line reader so
keybinds still work instead of leaking escape sequences into the buffer.
"""
import os
import sys
from contextlib import contextmanager
from typing import List, Optional, Iterator

SLASH_COMMANDS = [
    "/help", "/goal", "/stop", "/queue", "/sidebar", "/status", "/mode", "/perm", "/theme",
    "/provider", "/model", "/models", "/config", "/keys", "/setup",
    "/effort", "/todo", "/skills", "/reload", "/mcp", "/update", "/discord",
    "/subagent", "/agents", "/agent", "/back",
    "/compact", "/session", "/checkpoint", "/tokens", "/diff", "/clear",
    "/learn", "/exit", "/quit"
]

COMMAND_DESCRIPTIONS = {
    "/help": "Show commands, hotkeys & usage reference",
    "/goal": "Launch autonomous goal loop in Super Mode",
    "/stop": "Interrupt running task (or '/stop all' to clear queue)",
    "/queue": "View, drop, pause, resume or clear prompt queue",
    "/sidebar": "Toggle or view workspace, session & model sidebar",
    "/status": "Display full system, agent, budget & queue status",
    "/update": "Check for and apply latest updates from Git / PyPI",
    "/discord": "Manage Discord bot daemon and sync connection",
    "/mode": "Switch execution mode (plan, build, super)",
    "/perm": "Set permission level (secure, default, full)",
    "/theme": "Browse or switch color themes",
    "/provider": "Switch LLM provider (openai, anthropic, gemini, etc.)",
    "/model": "Switch model name for current provider",
    "/models": "Browse models catalog with token & thinking specs",
    "/config": "Inspect or modify persistent configuration",
    "/keys": "Manage provider API keys securely",
    "/setup": "Run interactive configuration setup wizard",
    "/effort": "Adjust thinking / reasoning effort level",
    "/todo": "Inspect or update session task list",
    "/skills": "Browse available agent skills catalog",
    "/reload": "Reload custom skills and MCP tools",
    "/mcp": "Manage Model Context Protocol servers",
    "/subagent": "Spawn a specialized subagent worker",
    "/agents": "Open interactive agent swarm board (F2)",
    "/agent": "View detailed log for a specific agent",
    "/back": "Return to parent conversation view (ESC)",
    "/compact": "Trigger proactive context window compaction",
    "/session": "Save, resume, fork, or export session",
    "/checkpoint": "Manage undo/redo checkpoints",
    "/tokens": "Inspect token consumption & context budget",
    "/diff": "View git diff of workspace changes",
    "/clear": "Clear terminal screen and redraw HUD",
    "/learn": "Extract and save persistent knowledge lessons",
    "/exit": "Save session and exit Harness",
    "/quit": "Save session and exit Harness",
}

VIEW_PARENT = "parent"
VIEW_AGENTS = "agents"

# Sentinel values returned by get_input() when a keybind is pressed.
SENTINEL_OPEN_AGENTS = "\x00__OPEN_AGENTS__"
SENTINEL_BACK = "\x00__BACK__"


@contextmanager
def no_echo_stdin(fd: Optional[int] = None) -> Iterator[None]:
    """Suppress terminal echo during agent runs.

    While an agent is streaming (tool calls / long responses) the terminal is
    in its normal echo mode, so every control key the user presses — Ctrl-C,
    ESC, arrow keys — is echoed by the driver as ``^C`` / ``^[`` noise on the
    line. This guard turns echo off for the duration of the run and flushes any
    stray keystrokes on exit, so the buffer never fills with control garbage.
    ISIG stays enabled, so Ctrl-C still raises KeyboardInterrupt as usual.

    ``fd`` defaults to stdin; pass an explicit fd only in tests.
    """
    if os.name == "nt":
        # Windows: use msvcrt to suppress echo via Console API
        try:
            import msvcrt  # noqa: F401
            import ctypes
            import ctypes.wintypes  # noqa: F401 — submodule, not exposed by `import ctypes`
            kernel32 = ctypes.windll.kernel32
            STD_INPUT_HANDLE = -10
            handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
            # Get current console mode
            mode = ctypes.wintypes.DWORD()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            ENABLE_ECHO_INPUT = 0x0004
            new_mode = mode.value & ~ENABLE_ECHO_INPUT
            kernel32.SetConsoleMode(handle, new_mode)
            try:
                yield
            finally:
                kernel32.SetConsoleMode(handle, mode.value)
        except Exception:
            yield
        return

    if fd is None:
        try:
            fd = sys.stdin.fileno()
            if not sys.stdin.isatty():
                yield
                return
        except Exception:
            yield
            return
    try:
        import termios
    except Exception:
        yield
        return

    # Foreground-process-group guard: termios.tcsetattr on a TTY from a
    # background process group raises SIGTTOU, which STOPs the whole process
    # (all threads) until someone resumes it — hanging the TUI forever. This
    # happens when harness runs /goal etc. in a background job (e.g. `harness &
    #`, CI, or test runners attached to a controlling terminal). Only manipulate
    # the terminal when we are actually in its foreground process group.
    try:
        if os.tcgetpgrp(fd) != os.getpgrp():
            yield
            return
    except Exception:
        # No controlling terminal / not a real tty — safe to proceed only if
        # tcgetattr works, which the next block verifies anyway.
        pass

    try:
        old = termios.tcgetattr(fd)
    except Exception:
        yield
        return

    try:
        new = termios.tcgetattr(fd)
        new[3] &= ~(termios.ECHO | termios.ECHOCTL | termios.ECHOE | termios.ECHOK)
        termios.tcsetattr(fd, termios.TCSANOW, new)
        yield
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSANOW, old)
            termios.tcflush(fd, termios.TCIFLUSH)
        except Exception:
            pass


def is_command_input(line: str, known_commands: dict or frozenset) -> bool:
    """Decide whether a line starting with ``/`` is a slash command or a plain
    prompt whose first token is a path (e.g. a file pasted/swiped into the buffer).

    Absolute paths, ``~/``, ``./``, ``../``, and ``file://`` prefixes route to the
    prompt so media attachments can be parsed; known commands and unknown command
    words (for the helpful "Unknown command" error) stay command routing.
    """
    if not line.startswith("/"):
        return False
    if line.startswith("//"):
        return False
    first = line.split(" ", 1)[0]
    if first[1:].lower() in known_commands:
        return True
    if _is_path_like_token(first):
        return False
    return True


def _is_path_like_token(token: str) -> bool:
    """Detect path-like tokens across all platforms (Unix /, Windows C:\\, UNC)."""
    if token.startswith(("~/", "./", "../", "file://")):
        return True
    # Unix absolute path
    if token.startswith("/"):
        rest = token[1:]
        if "/" in rest:
            return True
        return os.path.isfile(token) or os.path.isdir(token)
    # Windows absolute path: C:\..., D:\..., or UNC \\server\share
    if len(token) >= 2 and token[1] == ":" and token[2:3] in ("\\", "/"):
        return True
    if token.startswith("\\\\"):
        return True
    # Cross-platform: let os.path decide
    try:
        return os.path.isabs(token) and (os.path.isfile(token) or os.path.isdir(token))
    except Exception:
        return False


class InputHandler:
    """Provides interactive prompt with auto-completion and command history."""

    def __init__(self):
        self._has_prompt_toolkit = False
        self._pt_session = None
        self._pt_history = None
        self._pt_completer = None

        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter, Completer, Completion
            from prompt_toolkit.history import InMemoryHistory
            import os
            from pathlib import Path

            class SlashAndMentionCompleter(Completer):
                def __init__(self, slash_commands, command_meta=None):
                    self.slash_commands = slash_commands
                    self.command_meta = command_meta or {}

                def get_completions(self, document, complete_event):
                    text_before = document.text_before_cursor
                    # Mention path completion has priority: find last @ in text before cursor
                    # and ensure no whitespace between @ and cursor
                    if '@' in text_before:
                        at_index = text_before.rfind('@')
                        after_at = text_before[at_index+1:]
                        if not any(c.isspace() for c in after_at):
                            mention = '@' + after_at
                            prefix = mention[1:]
                            if '/' in prefix:
                                dir_idx = prefix.rfind('/')
                                dir_part = prefix[:dir_idx]
                                name_part = prefix[dir_idx+1:]
                                base_dir = Path(dir_part) if dir_part else Path('.')
                            else:
                                dir_part = ''
                                name_part = prefix
                                base_dir = Path('.')
                            if not base_dir.is_absolute():
                                base_dir = Path.cwd() / base_dir
                            try:
                                if base_dir.is_dir():
                                    for entry in sorted(base_dir.iterdir(), key=lambda e: e.name):
                                        entry_name = entry.name
                                        if entry_name.startswith(name_part):
                                            if dir_part:
                                                display = '@' + dir_part + '/' + entry_name
                                            else:
                                                display = '@' + entry_name
                                            if entry.is_dir():
                                                display += '/'
                                            yield Completion(display, start_position=-len(mention))
                            except Exception:
                                pass
                            return

                    # Slash command completion: check if the current token starts with /
                    word = document.get_word_before_cursor()
                    if word.startswith('/'):
                        for cmd in self.slash_commands:
                            if cmd.startswith(word):
                                meta = self.command_meta.get(cmd, "")
                                yield Completion(
                                    cmd,
                                    start_position=-len(word),
                                    display=cmd,
                                    display_meta=meta if meta else None,
                                )
                        return

            self._pt_completer = SlashAndMentionCompleter(SLASH_COMMANDS, COMMAND_DESCRIPTIONS)
            self._pt_history = InMemoryHistory()
            self._pt_session = PromptSession(completer=self._pt_completer, history=self._pt_history)
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

    def _create_session(self, view: str):
        """Build a PromptSession with the keybindings appropriate to the active view."""
        from prompt_toolkit import PromptSession, keys
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()
        if view == VIEW_PARENT:
            @kb.add(keys.Keys.F2)
            def _open_agents(event):
                event.app.exit(result=SENTINEL_OPEN_AGENTS)

            @kb.add("c-g")
            def _open_agents_alt(event):
                event.app.exit(result=SENTINEL_OPEN_AGENTS)
        else:
            @kb.add(keys.Keys.Escape)
            def _back(event):
                event.app.exit(result=SENTINEL_BACK)

        from prompt_toolkit.styles import Style
        pt_style = Style.from_dict({
            "bottom-toolbar": "noreverse noinherit",
            "bottom-toolbar.text": "noreverse noinherit",
        })

        return PromptSession(
            completer=self._pt_completer,
            history=self._pt_history,
            key_bindings=kb,
            style=pt_style,
        )

    def get_input(
        self,
        prompt_text: str = "Harness> ",
        view: str = VIEW_PARENT,
        bottom_toolbar=None,
        refresh_interval: Optional[float] = None,
        placeholder: Optional[str] = None,
    ) -> str:
        """Read a line of input from user with toolbar and completion support.

        In the agent-swarm board view, ESC returns to the parent view; in the
        parent view, F2 (or Ctrl+G) opens the agents board.
        """
        if self._has_prompt_toolkit:
            try:
                session = self._create_session(view)
                kwargs = {}
                if bottom_toolbar is not None:
                    kwargs["bottom_toolbar"] = bottom_toolbar
                if refresh_interval is not None:
                    kwargs["refresh_interval"] = refresh_interval
                if placeholder:
                    kwargs["placeholder"] = placeholder

                from prompt_toolkit.patch_stdout import patch_stdout
                with patch_stdout(raw=True):
                    result = session.prompt(prompt_text, **kwargs).strip()
                if result in (SENTINEL_OPEN_AGENTS, SENTINEL_BACK):
                    return result
                return result
            except EOFError:
                return "/exit"
            except KeyboardInterrupt:
                return SENTINEL_BACK if view == VIEW_AGENTS else "/stop"

        # Fallback without prompt_toolkit: raw terminal reader so keybinds work,
        # falling back to plain input() when stdin is not an interactive TTY.
        plain_str = str(prompt_text)
        if not isinstance(prompt_text, str):
            try:
                from prompt_toolkit.formatted_text import to_plain_text
                plain_str = to_plain_text(prompt_text)
            except Exception:
                plain_str = "│ "

        try:
            if sys.stdin.isatty():
                try:
                    return self._raw_line(plain_str, view)
                except (EOFError, KeyboardInterrupt):
                    return SENTINEL_BACK if view == VIEW_AGENTS else "/exit"
            return input(plain_str).strip()
        except (EOFError, KeyboardInterrupt):
            return SENTINEL_BACK if view == VIEW_AGENTS else "/exit"

    def classify_keypress(self, raw: bytes, view: str = VIEW_PARENT) -> Optional[str]:
        """Classify a partial/complete keypress buffer.

        Returns:
          - a sentinel string when the keypress is a complete keybind,
          - "" when the keypress is complete but not a keybind (swallow it),
          - None when more bytes are needed.
        """
        if raw == b"\x07":
            return SENTINEL_OPEN_AGENTS
        if raw == b"\x03":
            raise KeyboardInterrupt
        if raw == b"\x04":
            raise EOFError
        if raw.startswith(b"\x1b"):
            return self._classify_escape(raw)
        return None

    def _classify_escape(self, seq: bytes) -> Optional[str]:
        """Classify an escape sequence; '' swallows a complete non-keybind sequence."""
        if seq == b"\x1b":
            return None
        if seq in (b"\x1b[12~", b"\x1bOQ", b"\x1b[1;2Q"):
            return SENTINEL_OPEN_AGENTS
        if seq.startswith(b"\x1b["):
            if len(seq) == 2:
                return None
            tail = seq[2:]
            if tail.isdigit() or tail.endswith(b";"):
                return None
            if tail[-1] in range(0x40, 0x7F):
                return ""
            return None
        if seq.startswith(b"\x1bO"):
            return None if len(seq) == 2 else ""
        if len(seq) > 6:
            return ""
        return None

    def _raw_line(self, prompt_text: str, view: str) -> str:
        """Read one line from a raw-mode TTY so keybinds never leak into the buffer."""
        try:
            import os
            import select
            import termios
            import tty
        except ImportError:
            # Windows / platforms without termios: fall back to plain input().
            return input(prompt_text).strip()

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
        except termios.error:
            termios.tcsetattr(fd, termios.TCSAFLUSH, old)
            return input(prompt_text).strip()

        out = sys.stdout.fileno()
        os.write(out, f"\r\x1b[K{prompt_text}".encode())

        buf = ""
        seq = b""
        try:
            while True:
                if seq:
                    r, _, _ = select.select([fd], [], [], 0.05)
                    if not r:
                        seq = b""
                        if view == VIEW_AGENTS:
                            os.write(out, b"\r\n")
                            return SENTINEL_BACK
                        continue
                    seq += os.read(fd, 1)
                    decision = self.classify_keypress(seq, view)
                    if decision == SENTINEL_OPEN_AGENTS:
                        os.write(out, b"\r\n")
                        return SENTINEL_OPEN_AGENTS
                    if decision == "":
                        seq = b""
                    continue

                b = os.read(fd, 1)
                if b == b"\x1b":
                    seq = b"\x1b"
                    continue
                if b == b"\x07":
                    os.write(out, b"\r\n")
                    return SENTINEL_OPEN_AGENTS
                if b == b"\x03":
                    raise KeyboardInterrupt
                if b == b"\x04":
                    raise EOFError
                if b in (b"\r", b"\n"):
                    os.write(out, b"\r\n")
                    return buf
                if b in (b"\x7f", b"\x08"):
                    if buf:
                        buf = buf[:-1]
                        os.write(out, b"\b \b")
                    continue
                if b < b"\x20" or b >= b"\x80":
                    continue
                ch = b.decode("utf-8", errors="ignore")
                buf += ch
                os.write(out, ch.encode())
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, old)