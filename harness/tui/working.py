
"""
Cute working indicators — animated mascots & playful messages.

Pure, dependency-free helpers used by the terminal renderer and the
prompt_toolkit bottom toolbar so the user can always see *something*
is alive while the agent works. Every function is deterministic given
its inputs (no RNG, no wall-clock reads) so it is trivially testable
and safe to call from any thread.
"""
from typing import Sequence, Tuple

# ── Cadence constants ──────────────────────────────────────────────────
# The spinner (loading icon) and the sentence are animated on two
# INDEPENDENT cadences:
#
#   TICK_INTERVAL      → 0.12s per spinner frame (~8fps, icon only)
#   MESSAGE_INTERVAL   → 42 frames → ~5.0s per message (slow, calm)
#   MASCOT_INTERVAL    → 16 frames → ~2.0s per mascot swap
#
# The icon therefore keeps moving while the sentence stays put for a
# good while — no more flickering every second.
TICK_INTERVAL: float = 0.12        # seconds per spinner frame
MESSAGE_INTERVAL: int = 42         # frames between message rotations (~5s)
MASCOT_INTERVAL: int = 16          # frames between mascot swaps (~2s)

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
    """Pick a cute message for *activity* — cycles every ~5s.

    The message rotates on its own slow cadence (``MESSAGE_INTERVAL``
    ticks), independent of the spinner/mascot animation, which flips
    every tick. At the default 120ms tick rate that's a new message
    roughly every 5 seconds — calm, not flickery.
    """
    pool = messages_for(activity)
    return pool[(tick // MESSAGE_INTERVAL) % len(pool)]


def rotating_message(tick: int = 0, activity: str = "") -> str:
    """One of the 15+ funny full-sentence lines, rotating over time.

    Advances every ``MESSAGE_INTERVAL`` ticks (~5s at the default
    120ms tick rate) — deliberately much slower than the spinner,
    which updates independently every tick. When *activity* names a
    known pool, that pool's sentence is mixed in every 4th slot so
    tool-specific lines still appear.
    """
    slot = tick // MESSAGE_INTERVAL
    pool = messages_for(activity) if activity else ()
    if pool and slot % 4 == 3:
        return pool[(slot // 4) % len(pool)]
    return FUNNY_MESSAGES[slot % len(FUNNY_MESSAGES)]


def mascot_name(tick: int = 0) -> str:
    """Default rotating mascot — cycles every ``MASCOT_INTERVAL`` ticks (~2s)."""
    return MASCOT_ORDER[(tick // MASCOT_INTERVAL) % len(MASCOT_ORDER)]


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
    """Plain tool line: ``spinner mascot label`` — no cute custom message.

    Historically this appended a playful verb ("reading… view_file"),
    but tool invocation is reported factually by the renderer now
    ("Invoking Read: view_file"), so the helper must not reintroduce
    a mascot sentence next to it.
    """
    name = mascot_name(tick)
    line = f"{spinner_frame('braille', tick)} {mascot_frame(name, tick)}"
    return f"{line} {label}".strip() if label else line


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
    """Thread-safe, clock-driven animation driver.

    The tick is derived from elapsed wall time (single source of truth),
    so the renderer thread, the prompt repaint and the toolbar can never
    disagree or double-count. From that tick:

    * the **spinner glyph** advances every frame (~8fps), and
    * the **message** advances only every ``MESSAGE_INTERVAL`` frames
      (~5s) — completely independent of the icon.

    Elapsed time accumulates across stop/start cycles, so pausing the
    animation never resets the message rotation mid-cycle.
    """

    def __init__(self, activity: str = "thinking") -> None:
        self._tick = 0
        self._activity = activity
        self._running_since: float | None = None
        self._elapsed_base: float = 0.0

    @property
    def tick(self) -> int:
        return self._tick

    @property
    def activity(self) -> str:
        return self._activity

    def set_activity(self, activity: str) -> None:
        """Switch activity — the message rotation restarts for the new one."""
        self._activity = activity
        self._elapsed_base = 0.0
        if self._running_since is not None:
            import time as _time
            self._running_since = _time.monotonic()
        self._tick = 0

    def advance(self, steps: int = 1) -> int:
        """Advance the animation.

        While the wall clock is running the tick is derived from elapsed
        time (single source of truth), so the renderer thread and the
        prompt repaint can never disagree or double-count. Manual
        stepping only applies when the clock is stopped (tests).
        """
        if self._running_since is not None:
            return self.sync_tick()
        self._tick += steps
        return self._tick

    def line(self, tokens: int | None = None) -> str:
        if self._running_since is not None:
            self.sync_tick()
        if self._activity == "thinking" and tokens is not None:
            return thinking_line(self._tick, tokens)
        return working_line(self._activity, self._tick)

    # ── Time-driven (self-animating) ────────────────────────────────
    # The tick is derived from elapsed wall time so ANY caller that
    # repaints (prompt_toolkit's 0.5s refresh, the renderer's spinner
    # thread) gets a fresh frame without anyone having to advance it.
    # The spinner frame rate lives in the module-level TICK_INTERVAL so
    # the renderer thread and the animator agree on the cadence.

    def ensure_running(self) -> None:
        """Start the clock if it isn't already running."""
        import time as _time
        if self._running_since is None:
            self._running_since = _time.monotonic()

    def stop(self) -> None:
        """Stop the clock, keeping the accumulated elapsed time."""
        import time as _time
        if self._running_since is not None:
            self._elapsed_base += _time.monotonic() - self._running_since
            self._running_since = None

    @property
    def running(self) -> bool:
        return self._running_since is not None

    def elapsed(self) -> float:
        """Total animated seconds so far (across stop/start cycles)."""
        import time as _time
        total = self._elapsed_base
        if self._running_since is not None:
            total += _time.monotonic() - self._running_since
        return total

    def sync_tick(self) -> int:
        """Recompute the tick from elapsed time and return it."""
        self._tick = int(self.elapsed() / TICK_INTERVAL)
        return self._tick

    def live_line(self, tokens: int | None = None) -> str:
        """Current line — auto-advances the tick from wall time."""
        self.ensure_running()
        self.sync_tick()
        return self.line(tokens)
