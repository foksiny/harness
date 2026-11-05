
"""
Cute working indicators — animated mascots & playful messages.

Pure, dependency-free helpers used by the terminal renderer and the
prompt_toolkit bottom toolbar so the user can always see *something*
is alive while the agent works. Every function is deterministic given
its inputs (no RNG, no wall-clock reads) so it is trivially testable
and safe to call from any thread.
"""
from typing import Sequence, Tuple

# ── Mascots ──────────────────────────────────────────────────────────
# Each mascot is a tuple of frames (one per animation tick). Frames are
# chosen to be single cells wide where possible so they never break
# layout, and they avoid combining characters that some terminals
# render with variable width.

MASCOT_FRAMES: dict[str, Tuple[str, ...]] = {
    "cat": ("(=^･ω･^=)", "(=^･ェ･^=)", "(=^･ω･^=)", "(=^･ェ･^=)"),
    "bunny": ("(\\_/)", "(\\_/)", "(•ㅅ•)", "(\\_/)"),
    "frog": ("(─‿‿─)", "(─‿‿─)", "(•‿•)", "(─‿‿─)"),
    "bear": ("ʕ•ᴥ•ʔ", "ʕ•ᴥ•ʔ", "ʕ^ᴥ^ʔ", "ʕ•ᴥ•ʔ"),
    "panda": ("(｀-´)╯", "(｀-´)╯", "(`-´)╯", "(｀-´)╯"),
    "octopus": ("(╯°□°)╯", "(╯°□°)╯", "(╯°△°)╯", "(╯°□°)╯"),
    "fox": ("(•ᴗ•)", "(•ᴗ•)", "(•ᴗ•)", "(◕ᴗ◕)"),
    "star": ("✿", "❀", "✿", "❀"),
    "sparkles": ("✧", "✦", "✧", "✦"),
    "heart": ("♥", "♡", "♥", "♡"),
    "moon": ("☾", "☽", "☾", "☽"),
    "blob": ("(｡•́︿•̀)｡", "(｡•́︿•̀)｡", "(｡•́︿•̀)｡", "(｡•́︿•̀)｡"),
}

# Ordered list — the rotating default cycles through these.
MASCOT_ORDER: Tuple[str, ...] = (
    "cat", "bunny", "frog", "bear", "fox", "octopus",
    "panda", "blob", "star", "sparkles", "heart", "moon",
)

# ── Spinner dots ─────────────────────────────────────────────────────
# Classic braille spinner, plus a couple of playful alternatives.
SPINNERS: dict[str, Tuple[str, ...]] = {
    "braille": ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"),
    "dots": ("⠁", "⠂", "⠄", "⡀", "⢀", "⠠", "⠐", "⠈"),
    "arrows": ("←", "↖", "↑", "↗", "→", "↘", "↓", "↙"),
    "hearts": ("♥", "♡", "♥", "♡", "♥", "♡"),
    "blocks": ("▁", "▃", "▅", "▆", "▇", "▅", "▃", "▁"),
}

# ── Cute messages ────────────────────────────────────────────────────
# Grouped by activity so the renderer can pick the right flavour.
# Messages are short, warm, and — importantly — always paired with a
# concrete verb so the user knows what is happening.

MESSAGES: dict[str, Tuple[str, ...]] = {
    "thinking": (
        "thinking", "pondering", "daydreaming", "noodling",
        "brainstorming", "mulling it over", "connecting the dots",
        "consulting the brain cells", "staring into the void",
        "overthinking (in a good way)",
    ),
    "reading": (
        "reading", "peeking", "scanning", "squinting at",
        "borrowing", "sniffing out", "reading aloud (quietly)",
    ),
    "writing": (
        "writing", "scribbling", "typing furiously", "polishing",
        "inking", "drafting", "sprinkling magic on",
    ),
    "searching": (
        "searching", "hunting", "sniffing around", "digging",
        "rummaging through", "playing detective", "following the trail",
    ),
    "running": (
        "running", "zooming", "sprinting", "revving up",
        "warming up", "doing the thing", "crunching",
    ),
    "waiting": (
        "waiting", "patiently waiting", "twiddling thumbs",
        "holding my breath", "binge-watching the progress",
        "keeping my paws warm",
    ),
    "generating": (
        "generating", "conjuring", "whipping up", "manifesting",
        "materialising", "brewing", "cooking",
    ),
    "fixing": (
        "fixing", "patching", "untangling", "un-borking",
        "putting a band-aid on", "duct-taping (professionally)",
        "exorcising the bug",
    ),
    "testing": (
        "testing", "double-checking", "poking with a stick",
        "making sure it works", "crossing fingers", "proofreading",
    ),
    "celebrating": (
        "done", "ta-da", "nailed it", "beautiful", "chef's kiss",
        "smooth sailing", "all good",
    ),
}

# Generic fallback for any activity not in MESSAGES.
_GENERIC: Tuple[str, ...] = (
    "working", "busy", "on it", "faithfully working",
    "hard at work", "in the zone", "plugging away",
)

# ── The big rotating show ────────────────────────────────────────────
# 15+ funny full-sentence lines shown ABOVE the prompt box while the
# agent is working. These are deliberately sentence-shaped (not single
# verbs): the user asked for messages like "Cooking up your project"
# / "Trying not to explode the tests". The pool mixes work-flavoured
# lines with the old verb pools so activity-specific lines still appear
# when a tool is running; ``rotating_message`` cycles through them all.
FUNNY_MESSAGES: Tuple[str, ...] = (
    "Cooking up your project",
    "Trying not to explode the tests",
    "Warming up the brain cells",
    "Polishing every pixel",
    "Asking the rubber duck nicely",
    "Herding scattered semicolons",
    "Bribing the compiler with compliments",
    "Consulting the ancient scrolls",
    "Feeding the hamsters that power this",
    "Untangling someone else's spaghetti",
    "Counting to ten (twice)",
    "Sneaking past the linter",
    "Watering the code so it grows",
    "Summoning the good bugs away",
    "Teaching the machine to be nice",
    "Knocking politely on the API door",
    "Rearranging the deck chairs",
    "Making it work, then making it work well",
)


def messages_for(activity: str) -> Tuple[str, ...]:
    """Return the message pool for *activity*, or the generic pool."""
    return MESSAGES.get(activity, _GENERIC)


# ── Core deterministic helpers ───────────────────────────────────────

def spinner_frame(name: str = "braille", tick: int = 0) -> str:
    """One spinner glyph for *tick* (wraps around the frame list)."""
    frames = SPINNERS.get(name, SPINNERS["braille"])
    return frames[tick % len(frames)]


def mascot_frame(name: str = "cat", tick: int = 0) -> str:
    """One mascot frame for *tick*."""
    frames = MASCOT_FRAMES.get(name, MASCOT_FRAMES["cat"])
    return frames[tick % len(frames)]


def message_for(activity: str = "thinking", tick: int = 0) -> str:
    """Pick a cute message for *activity* — cycles every few ticks.

    The message index advances every 4 ticks so messages change
    roughly every second at the default 250ms tick rate, instead of
    flickering on every frame.
    """
    pool = messages_for(activity)
    return pool[(tick // 4) % len(pool)]


def rotating_message(tick: int = 0, activity: str = "") -> str:
    """One of the 15+ funny full-sentence lines, rotating over time.

    Cycles every 12 ticks (~3s at the default 250ms prompt refresh),
    so the line above the prompt box feels alive without flickering.
    When *activity* names a known pool, that pool's sentence is mixed
    in every 4th slot so tool-specific lines still appear.
    """
    slot = tick // 12
    pool = messages_for(activity) if activity else ()
    if pool and slot % 4 == 3:
        return pool[(slot // 4) % len(pool)]
    return FUNNY_MESSAGES[slot % len(FUNNY_MESSAGES)]


def mascot_name(tick: int = 0) -> str:
    """Default rotating mascot — cycles every 8 ticks (~2s)."""
    return MASCOT_ORDER[(tick // 8) % len(MASCOT_ORDER)]


def working_line(
    activity: str = "thinking",
    tick: int = 0,
    spinner: str = "braille",
    mascot: str | None = None,
) -> str:
    """Build one complete ``spinner mascot message…`` working line.

    Example::

        >>> working_line("thinking", 0)
        '⠋ (=^･ω･^=) thinking…'

    The mascot defaults to the rotating mascot for *tick*.
    """
    name = mascot if mascot is not None else mascot_name(tick)
    return f"{spinner_frame(spinner, tick)} {mascot_frame(name, tick)} {rotating_message(tick, activity)}…"


def thinking_line(tick: int = 0, tokens: int = 0) -> str:
    """Cute thinking line with a live token count (braille spinner)."""
    return f"{working_line('thinking', tick)} (~{tokens:,} tokens)"


def tool_line(activity: str, tick: int = 0, label: str = "") -> str:
    """Cute tool line, e.g. ``⠙ 📖 reading… view_file``."""
    return f"{working_line(activity, tick)} {label}".strip() if label else working_line(activity, tick)


# ── Activity classification ──────────────────────────────────────────

_TOOL_ACTIVITIES: Sequence[Tuple[Sequence[str], str]] = (
    (("read", "view", "cat", "get_file"), "reading"),
    (("edit", "write", "replace", "patch"), "writing"),
    (("search", "find", "grep", "glob"), "searching"),
    (("bash", "cmd", "shell", "run_command", "terminal", "exec"), "running"),
    (("ask_user", "question", "ask"), "waiting"),
    (("test", "check", "verify"), "testing"),
    (("fix", "repair"), "fixing"),
    (("subagent", "swarm"), "generating"),
)


def activity_for_tool(tool_name: str) -> str:
    """Map a tool name to a cute activity key."""
    nl = (tool_name or "").lower()
    for keys, activity in _TOOL_ACTIVITIES:
        if any(k in nl for k in keys):
            return activity
    return "generating"


# ── Animation driver ─────────────────────────────────────────────────

class WorkingAnimator:
    """Thread-safe tick counter shared between renderer & toolbar.

    The renderer increments the tick (e.g. from a background thread or
    on each throttled header refresh); the prompt_toolkit toolbar reads
    :meth:`line` on its 0.5s repaint and gets a fresh frame for free.
    The tick advances on its own via :meth:`ensure_running`, so the
    line above the prompt box keeps animating for as long as the agent
    is working — even between token bursts.
    """

    def __init__(self, activity: str = "thinking") -> None:
        self._tick = 0
        self._activity = activity
        self._running_since: float | None = None

    @property
    def tick(self) -> int:
        return self._tick

    @property
    def activity(self) -> str:
        return self._activity

    def set_activity(self, activity: str) -> None:
        self._activity = activity
        self._tick = 0

    def advance(self, steps: int = 1) -> int:
        self._tick += steps
        return self._tick

    def line(self, tokens: int | None = None) -> str:
        if self._activity == "thinking" and tokens is not None:
            return thinking_line(self._tick, tokens)
        return working_line(self._activity, self._tick)

    # ── Time-driven (self-animating) ────────────────────────────────
    # The tick is derived from elapsed wall time so ANY caller that
    # repaints (prompt_toolkit's 0.5s refresh, the renderer's spinner
    # thread) gets a fresh frame without anyone having to advance it.
    TICK_INTERVAL: float = 0.25  # seconds per animation frame

    def ensure_running(self) -> None:
        """Start the clock if it isn't already running."""
        import time as _time
        if self._running_since is None:
            self._running_since = _time.monotonic()
            self._tick = 0

    def stop(self) -> None:
        self._running_since = None
        self._tick = 0

    @property
    def running(self) -> bool:
        return self._running_since is not None

    def sync_tick(self) -> int:
        """Recompute the tick from elapsed time and return it."""
        import time as _time
        if self._running_since is None:
            return self._tick
        self._tick = int((_time.monotonic() - self._running_since) / self.TICK_INTERVAL)
        return self._tick

    def live_line(self, tokens: int | None = None) -> str:
        """Current line — auto-advances the tick from wall time."""
        self.ensure_running()
        return self.line(tokens) if self.sync_tick() or True else ""
