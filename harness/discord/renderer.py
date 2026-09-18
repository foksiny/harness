"""
Discord renderer for Harness agent events.

Adapts Harness's streaming agent events to Discord's message model:

- **Thinking** is accumulated and delivered as a Discord quote block (each line
  prefixed with ``> ``), flushed when the model starts speaking or calls a tool.
- **The final response is not streamed**: text deltas are accumulated and the
  complete answer is sent in full at the end of the turn, chunked to Discord's
  2000-character message limit.
- **Tool usage is reported live** with a ``🔧`` notice so the channel sees what
  is running, plus a concise first-line result.

This module has no ``discord.py`` dependency so it can be unit-tested without a
live Discord connection.
"""
from dataclasses import dataclass
from typing import List

DISCORD_MAX_MESSAGE_LEN = 2000


@dataclass
class DiscordOutgoing:
    """A message to send to Discord, produced by the renderer."""

    kind: str  # "thinking" | "response" | "notice" | "attachment" | "error"
    text: str


def chunk_message(text: str, max_len: int = DISCORD_MAX_MESSAGE_LEN) -> List[str]:
    """Split ``text`` into Discord-sized chunks, preferring newline boundaries.

    Uses a 10-char safety margin to account for Discord rendering edge cases
    (invisible markdown parsers, trailing newlines added by the client, etc.).
    """
    text = text or ""
    if not text:
        return []
    safe = max(10, max_len - 10)
    if len(text) <= safe:
        return [text]
    chunks: List[str] = []
    while text:
        if len(text) <= safe:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, safe)
        if cut <= 0:
            cut = safe
        chunks.append(text[:cut])
        text = text[cut:]
    return chunks


def _quote_block(lines: List[str]) -> str:
    return "\n".join(f"> {line}" for line in lines)


def _quote_block_len(lines: List[str]) -> int:
    """Length of the rendered quote block, including the ``> `` prefixes and
    newline separators."""
    return sum(len(line) + 2 for line in lines) + max(0, len(lines) - 1)


def chunk_quote(text: str, max_len: int = DISCORD_MAX_MESSAGE_LEN) -> List[str]:
    """Chunk thinking text into quote-block messages (``> `` per line).

    Each returned chunk is already formatted as a Discord quote block and stays
    under ``max_len`` even accounting for the two-character ``> `` prefix.
    """
    if not text:
        return []
    chunks: List[str] = []
    current: List[str] = []
    for line in (text or "").split("\n"):
        line_len = len(line) + 2  # '> ' prefix
        # An oversized line must be split on its own across multiple messages.
        if line_len > max_len:
            if current:
                chunks.append(_quote_block(current))
                current = []
            for piece in chunk_message(line, max_len - 2):
                chunks.append(_quote_block(piece.split("\n")))
            continue
        if current and _quote_block_len(current) + line_len + 1 > max_len:
            chunks.append(_quote_block(current))
            current = []
        current.append(line)
    if current:
        chunks.append(_quote_block(current))
    return chunks


TOOL_ICONS = {
    "bash": "⚡",
    "run_command": "⚡",
    "write_to_file": "📝",
    "replace_file_content": "📝",
    "edit_file": "📝",
    "grep_search": "🔍",
    "find_by_name": "🔍",
    "view_file": "📖",
    "read_url_content": "🌐",
    "search_web": "🌐",
    "git": "🌿",
    "subagent": "🤖",
    "ask_user": "❓",
}


def format_capsule_card(mode: str, model: str, duration: float, tokens: int = 0) -> str:
    """Render OpenCode turn capsule text for Discord."""
    dur_str = f"{duration:.1f}s" if duration >= 1.0 else f"{int(duration * 1000)}ms"
    tok_str = f" · `{tokens:,}t`" if tokens > 0 else ""
    return f"▣ **{mode.upper()}** · `{model}` · `{dur_str}`{tok_str}"


class DiscordRenderState:
    """Accumulates agent events into outgoing Discord messages.

    Feed it events with :meth:`add_event` (from either the agent generator or
    its event callback) and call :meth:`finish` at the end of a turn. Returns
    :class:`DiscordOutgoing` messages to send to the channel.
    """

    def __init__(self, max_message_len: int = DISCORD_MAX_MESSAGE_LEN):
        self.max_message_len = max_message_len
        self._thinking: List[str] = []
        self._response: List[str] = []
        self._thinking_flushed = False
        self._response_flushed = False

    @property
    def thinking(self) -> str:
        return "".join(self._thinking)

    @property
    def response(self) -> str:
        return "".join(self._response)

    def add_event(self, ev) -> List[DiscordOutgoing]:
        """Consume one agent event; return the Discord messages it triggers."""
        etype = getattr(ev, "type", None)
        data = getattr(ev, "data", None)

        if etype == "reasoning_delta":
            if self._thinking_flushed:
                # A fresh thinking phase begins after a previous flush.
                self._thinking = []
                self._thinking_flushed = False
            self._thinking.append(str(data))
            return []

        if etype == "text_delta":
            out = self._flush_thinking()
            self._response.append(str(data))
            return out

        if etype == "tool_call_start":
            out = self._flush_thinking()
            name = (data or {}).get("name", "tool")
            args = (data or {}).get("arguments", {}) or {}
            icon = TOOL_ICONS.get(name.lower(), "🔧")
            line = f"{icon} Using `{name}`"
            if args:
                preview = str(args)
                if len(preview) > 120:
                    preview = preview[:120] + "…"
                line += f"  `{preview}`"
            out.append(DiscordOutgoing("notice", line))
            return out

        if etype == "tool_call_result":
            out = self._flush_thinking()
            res = str((data or {}).get("result", ""))
            first = res.strip().split("\n")[0][:200] if res.strip() else "(empty)"
            out.append(DiscordOutgoing("notice", f"✔ {first}"))
            return out

        if etype == "mention":
            out = self._flush_thinking()
            resolved = str((data or {}).get("resolved", ""))
            kind = (data or {}).get("kind", "")
            icon = "📁" if kind == "folder" else "📄"
            out.append(DiscordOutgoing("attachment", f"{icon} Mentioned: `{resolved}`"))
            return out

        if etype == "mention_warning":
            out = self._flush_thinking()
            out.append(DiscordOutgoing("notice", f"⚠️ {str((data or {}).get('message', 'unknown mention issue'))}"))
            return out

        if etype == "attachment":
            out = self._flush_thinking()
            for f in (data or {}).get("files", []):
                kind = "🖼️ image" if f.get("type") == "image" else "🎞️ video"
                out.append(DiscordOutgoing("attachment", f"{kind} attached: `{f.get('path', '')}`"))
            return out

        if etype == "attachment_warning":
            out = self._flush_thinking()
            out.append(DiscordOutgoing("notice", f"⚠️ {str((data or {}).get('message', ''))}"))
            return out

        if etype == "vfb_notice":
            out = self._flush_thinking()
            provider = str((data or {}).get("provider", ""))
            model = str((data or {}).get("model", ""))
            out.append(DiscordOutgoing("notice", f"🕶️ Vision fallback: `{provider} ({model})`"))
            return out

        if etype == "vfb_result":
            out = self._flush_thinking()
            provider = str((data or {}).get("provider", ""))
            out.append(DiscordOutgoing("notice", f"✔ Vision fallback `{provider}` description embedded"))
            return out

        if etype == "compaction":
            out = self._flush_thinking()
            before = (data or {}).get("before_tokens", 0)
            after = (data or {}).get("after_tokens", 0)
            out.append(DiscordOutgoing("notice", f"📦 Context auto-compacted: {before:,} → {after:,} tokens"))
            return out

        if etype == "step_end":
            return self._flush_thinking()

        if etype == "error":
            return self._flush_thinking() + [
                DiscordOutgoing("error", f"❌ {(data or {}).get('message', 'Unknown error')}")
            ]

        if etype == "security_warning":
            return [DiscordOutgoing("notice", f"🛡️ {(data or {}).get('message', '')}")]

        if etype == "turn_complete":
            return self.finish()

        return []

    def finish(self) -> List[DiscordOutgoing]:
        """Flush any remaining thinking and the full response."""
        out = self._flush_thinking()
        out.extend(self._flush_response())
        return out

    def _flush_thinking(self) -> List[DiscordOutgoing]:
        if self._thinking_flushed or not self._thinking:
            return []
        self._thinking_flushed = True
        text = "".join(self._thinking).strip()
        if not text:
            return []
        return [DiscordOutgoing("thinking", text)]

    def _flush_response(self) -> List[DiscordOutgoing]:
        if self._response_flushed:
            return []
        self._response_flushed = True
        text = "".join(self._response).strip()
        if not text:
            return []
        return [DiscordOutgoing("response", text)]