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
    "/help", "/goal", "/mode", "/perm", "/theme",
    "/provider", "/model", "/models", "/config", "/keys", "/setup",
    "/effort", "/todo", "/skills", "/mcp",
    "/subagent", "/agents", "/agent", "/back",
    "/compact", "/session", "/checkpoint", "/tokens", "/diff", "/clear",
    "/learn", "/exit", "/quit"
]

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
    if token.startswith(("~/", "./", "../", "file://")):
        return True
    if token.startswith("/"):
        rest = token[1:]
        if "/" in rest:
            return True
        return os.path.isfile(token) or os.path.isdir(token)
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
                def __init__(self, slash_commands):
                    self.slash_completer = WordCompleter(slash_commands, sentence=True)

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
                                    for entry in base_dir.iterdir():
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
                        yield from self.slash_completer.get_completions(document, complete_event)
                        return
                        # Find last @ not preceded by whitespace? Simple rfind
                        at_index = text_before.rfind('@')
                        # Ensure the @ is part of current token (no whitespace after @)
                        after_at = text_before[at_index+1:]
                        if not any(c.isspace() for c in after_at):
                            # We have a mention in progress
                            # Extract the partial mention string
                            mention = '@' + after_at
                            # Determine base directory and prefix
                            prefix = mention[1:]  # strip @
                            if '/' in prefix:
                                # Path with directory component
                                # Find last slash
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
                                    for entry in base_dir.iterdir():
                                        entry_name = entry.name
                                        if entry_name.startswith(name_part):
                                            # Build display text
                                            if dir_part:
                                                display = '@' + dir_part + '/' + entry_name
                                            else:
                                                display = '@' + entry_name
                                            if entry.is_dir():
                                                display += '/'
                                            # Replacement start position is -len(mention)
                                            yield Completion(display, start_position=-len(mention))
                            except Exception:
                                pass
                            return
                    # Fallback to slash completer
                    yield from self.slash_completer.get_completions(document, complete_event)

            self._pt_completer = SlashAndMentionCompleter(SLASH_COMMANDS)
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

        return PromptSession(
            completer=self._pt_completer,
            history=self._pt_history,
            key_bindings=kb,
        )

    def get_input(self, prompt_text: str = "Harness> ", view: str = VIEW_PARENT) -> str:
        """Read a line of input from user.

        In the agent-swarm board view, ESC returns to the parent view; in the
        parent view, F2 (or Ctrl+G) opens the agents board.
        """
        if self._has_prompt_toolkit:
            try:
                session = self._create_session(view)
                result = session.prompt(prompt_text).strip()
                if result in (SENTINEL_OPEN_AGENTS, SENTINEL_BACK):
                    return result
                return result
            except (EOFError, KeyboardInterrupt):
                return SENTINEL_BACK if view == VIEW_AGENTS else "/exit"

        # Fallback without prompt_toolkit: raw terminal reader so keybinds work,
        # falling back to plain input() when stdin is not an interactive TTY.
        try:
            if sys.stdin.isatty():
                try:
                    return self._raw_line(prompt_text, view)
                except (EOFError, KeyboardInterrupt):
                    return SENTINEL_BACK if view == VIEW_AGENTS else "/exit"
            return input(prompt_text).strip()
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
        import os
        import select
        import termios
        import tty

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