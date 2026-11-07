"""Pure GUI safety guards shared by every desktop backend.

The failure these guards exist for is platform-independent:

    screenshot -> "looks right" -> type -> Enter -> the keystrokes went to a
    different surface (app launcher, overview search, another window) and the
    text was submitted there.

Two facts make that possible on every OS/DE:

1. Keyboard input follows *focus*, not the screenshot. A stale or misread
   screenshot says nothing about where the next keystroke lands.
2. ``Enter`` is what turns a harmless stray string into an action (send a
   message, launch the top search hit, confirm a dialog).

So the guards here answer three questions without touching a display:

* ``typing_preflight`` — is it safe to type at all right now? (overview /
  launcher / stale screen / expected target not visible)
* ``locate_typed_text`` — after typing, did the text actually appear, and
  where?
* ``check_landed`` — did it appear *near the field the agent clicked*, rather
  than somewhere else on screen (the GNOME-search-typed-the-message case)?

Everything degrades to "cannot verify" instead of raising, so a machine
without tesseract still gets the overview guard and the post-type checks.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from harness.computer.grounding import (
    detect_overview,
    fold_text,
    is_escape_combo,
    is_escape_key,
    typed_text_present,
)

# A screenshot older than this cannot justify a keystroke.
TYPING_MAX_CAPTURE_AGE = 20.0
# A click older than this is no longer a reliable "this is the input I meant".
CLICK_ANCHOR_MAX_AGE = 120.0
# Vertical slack around the clicked field, as a fraction of screen height.
# Generous on purpose: inputs move a little when a window is focused, but a
# keystroke that lands in the top bar / launcher is hundreds of pixels away.
ANCHOR_TOLERANCE_RATIO = 0.18
ANCHOR_TOLERANCE_MIN_PX = 120


def _deny(kind: str, reason: str, next_step: str = "", **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "allow": False,
        "ok": False,
        "kind": kind,
        "reason": reason,
        "next": next_step,
    }
    out.update(extra)
    return out


def _allow(**extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"allow": True, "ok": True, "kind": "ok", "reason": ""}
    out.update(extra)
    return out


def marks_text(marks: Optional[List[Dict[str, Any]]]) -> str:
    """Flatten OCR marks to one searchable string (never raises)."""
    parts: List[str] = []
    for mark in marks or []:
        if isinstance(mark, dict):
            parts.append(str(mark.get("name") or mark.get("text") or ""))
        else:
            parts.append(str(mark))
    return "\n".join(parts)


def typing_preflight(
    *,
    marks: Optional[List[Dict[str, Any]]],
    state: Optional[Dict[str, Any]] = None,
    expect: Optional[str] = None,
) -> Dict[str, Any]:
    """Decide whether typing is safe, from the freshest capture + screen state.

    ``state`` is ``ComputerController.screen_state()``. A missing state is
    treated as "no screenshot" (deny), because typing blind is exactly the
    bug this guard exists to prevent.
    """
    state = state or {}
    if not state.get("has_screenshot"):
        return _deny(
            "no_screenshot",
            "No screenshot has been captured, so the focused window is unknown.",
            "Call computer_screenshot, click the target input, then type.",
        )

    age = state.get("capture_age_seconds")
    if age is None or float(age) > TYPING_MAX_CAPTURE_AGE:
        return _deny(
            "stale_screenshot",
            f"The last screenshot is {age if age is not None else '?'}s old; "
            "focus may have changed.",
            "Call computer_screenshot again before typing.",
        )

    if state.get("overview"):
        return _deny(
            "overview_open",
            str(state.get("overview_reason")
                or "An overview/app-search surface is open."),
            "Press Escape, click the real app window, then re-screenshot "
            "and click the input field before typing. Do NOT type here.",
            overview=True,
        )

    text = marks_text(marks)
    if expect:
        if not fold_text(text):
            # No OCR: cannot prove the target is wrong, but cannot prove it is
            # right either. The post-type anchor check still applies.
            return _allow(verified=False, ocr=False,
                          warning="No OCR text available; target not verified by 'expect'.")
        if not typed_text_present(text, expect):
            sample = " ".join(fold_text(text).split())[:180]
            return _deny(
                "target_not_visible",
                f"Expected text {expect!r} is not visible in the current window; "
                "the frontmost app is probably not the intended target.",
                "Bring the target window to the front (click it in the screenshot) "
                "and re-screenshot. Do not type.",
                screen_text_sample=sample,
            )

    if not text.strip():
        return _allow(verified=False, ocr=False,
                      warning="No OCR text available; focus cannot be verified before typing.")
    return _allow(verified=True, ocr=True)


def locate_typed_text(
    marks: Optional[List[Dict[str, Any]]],
    text: str,
    prefer_y: Optional[int] = None,
) -> Dict[str, Any]:
    """Find the typed text in fresh OCR marks.

    Returns ``present`` (tolerant full-text match), ``located`` (we can point
    at a mark), ``candidates`` (all plausible marks) and the best mark's
    ``x``/``y``. ``prefer_y`` biases the best mark toward the clicked field so
    an older occurrence of the same word in the transcript does not win.
    Never raises.
    """
    marks = list(marks or [])
    all_text = marks_text(marks)
    if not all_text.strip():
        return {"present": False, "located": False, "ocr": False,
                "x": None, "y": None, "mark": None, "candidates": []}

    present = bool(typed_text_present(all_text, text))
    folded = fold_text(text)
    # Short strings ("hi", "ok") must be a whole token, otherwise they match
    # every headline that contains the letters. Long text keeps the tolerant
    # matcher because OCR wraps and mangles it.
    if len(folded) <= 8:
        import re as _re
        # Unicode-aware token boundaries (\w is Unicode-aware for str):
        # "olá" folds to "ola", CJK stays CJK, so [a-z0-9] alone would let
        # " Olá" match inside "Solá" and a 2-char CJK string match inside a
        # longer one. \w covers letters/digits from every script.
        present = bool(_re.search(
            r"(?<!\w)" + _re.escape(folded) + r"(?!\w)", fold_text(all_text)
        ))
    if not present:
        return {"present": False, "located": False, "ocr": True,
                "x": None, "y": None, "mark": None, "candidates": []}

    head = folded[:24].strip()
    words = [w for w in folded.replace("\n", " ").split() if len(w) >= 5][:10]
    candidates: List[Dict[str, Any]] = []
    for mark in marks:
        if not isinstance(mark, dict):
            continue
        name = fold_text(mark.get("name") or mark.get("text") or "")
        if not name:
            continue
        score = 0
        if head and head in name:
            score += 100
        score += sum(1 for w in words if w in name)
        if len(folded) <= 8:
            score += 50 if folded in name else -50
        if score <= 0:
            continue
        try:
            candidates.append({
                "score": score,
                "x": int(mark.get("x")),
                "y": int(mark.get("y")),
                "mark": mark,
            })
        except (TypeError, ValueError):
            continue

    if not candidates:
        return {"present": True, "located": False, "ocr": True,
                "x": None, "y": None, "mark": None, "candidates": []}

    def _rank(c: Dict[str, Any]) -> float:
        score = float(c["score"])
        if prefer_y is not None:
            score -= abs(int(c["y"]) - int(prefer_y)) / 1000.0
        return score

    best = max(candidates, key=_rank)
    return {"present": True, "located": True, "ocr": True,
            "x": best["x"], "y": best["y"], "mark": best["mark"],
            "score": best["score"], "candidates": candidates}


def check_landed(
    marks: Optional[List[Dict[str, Any]]],
    text: str,
    *,
    anchor: Optional[Dict[str, Any]] = None,
    screen_height: int = 0,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Did the typed text land where the agent clicked?

    ``anchor`` is the controller's last click (``{"x", "y", "ts"}``). When it
    is fresh and the text is locatable, at least one occurrence must lie
    within a vertical band around the clicked field. This is what distinguishes
    "typed into the Discord input" from "typed into the GNOME overview search",
    even when overview detection itself failed (OCR missed the placeholder).
    """
    import time as _time
    now_ts = _time.time() if now is None else float(now)
    anchor_y: Optional[int] = None
    anchor_checked = False
    if anchor and anchor.get("y") is not None:
        try:
            anchor_age = now_ts - float(anchor.get("ts") or 0)
            if 0 <= anchor_age <= CLICK_ANCHOR_MAX_AGE:
                anchor_y = int(anchor["y"])
        except (TypeError, ValueError):
            anchor_y = None

    located = locate_typed_text(marks, text, prefer_y=anchor_y)
    if not located["present"]:
        if located.get("ocr") is False:
            return {
                "ok": False,
                "delivered": None,
                "verified": False,
                "kind": "no_ocr",
                "reason": (
                    "The text could not be verified: OCR is unavailable (tesseract "
                    "missing) or the screen has no readable text."
                ),
                "next": (
                    "Install tesseract-ocr for automatic verification, then "
                    "re-screenshot and retry. Do NOT assume this was sent."
                ),
                "located": located,
                "anchor_checked": False,
            }
        return {
            "ok": False,
            "delivered": False,
            "verified": True,
            "kind": "text_not_on_screen",
            "reason": (
                "The typed text did not appear anywhere on screen. The keystrokes "
                "went somewhere that did not accept them (wrong focus, no input "
                "field, or a blocked/read-only surface)."
            ),
            "next": (
                "Do NOT press Return. Re-screenshot, click the actual input field, "
                "and retry. If an overview/launcher is open, press Escape first."
            ),
            "located": located,
            "anchor_checked": False,
        }

    if anchor_y is not None and located.get("located"):
        tolerance = max(
            ANCHOR_TOLERANCE_MIN_PX,
            int(float(screen_height or 0) * ANCHOR_TOLERANCE_RATIO),
        )
        candidate_ys = [int(c["y"]) for c in located.get("candidates") or [] if c.get("y") is not None]
        anchor_checked = True
        if candidate_ys and not any(abs(y - anchor_y) <= tolerance for y in candidate_ys):
            nearest = min(candidate_ys, key=lambda y: abs(y - anchor_y))
            dy = abs(nearest - anchor_y)
            return {
                "ok": False,
                "delivered": False,
                "verified": True,
                "kind": "text_landed_wrong_place",
                "reason": (
                    f"The text appeared {dy}px away from the field you clicked "
                    f"(tolerance {tolerance}px). It was captured by another "
                    "surface — typically an overview/app-search bar or a "
                    "different window — so it was NOT delivered to the target."
                ),
                "next": (
                    "Do NOT press Return. Press Escape, click the intended app "
                    "window/input in a fresh screenshot, and retry."
                ),
                "located": located,
                "anchor_checked": True,
                "anchor_y": anchor_y,
                "tolerance_px": tolerance,
            }

    return {
        "ok": True,
        "delivered": True,
        "verified": True,
        "kind": "ok",
        "reason": "",
        "located": located,
        "anchor_checked": anchor_checked,
    }


def keyboard_allowed(
    *,
    key: Optional[str] = None,
    keys: Optional[List[str]] = None,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Guard non-text keys. Escape is always allowed (it is the bail-out).

    Blocking ``Return`` on an overview/launcher surface is the difference
    between "typed a harmless string" and "launched the top search hit".
    """
    state = state or {}
    letters = []
    if key:
        letters.append(str(key))
    if keys:
        letters.extend(str(k) for k in keys)
    if is_escape_key(key) or is_escape_combo(keys or []):
        return _allow(verified=False, kind="escape")
    if state.get("overview"):
        return _deny(
            "overview_open",
            str(state.get("overview_reason")
                or "An overview/app-search surface is open."),
            "Press Escape, click the real app window, then re-screenshot. "
            "Do not send keys into the overview.",
            overview=True,
        )
    if state.get("typing_blocked"):
        return _deny(
            "typing_unverified",
            str(state.get("typing_blocked_reason")
                or "The last typed text could not be verified in the intended input."),
            "Do NOT press Return. Press Escape, re-screenshot, click the real "
            "input, and retype with verification (computer_type(expect=...)).",
        )
    if not state.get("has_screenshot"):
        return _deny(
            "no_screenshot",
            "No screenshot has been captured, so the focused window is unknown.",
            "Call computer_screenshot first; then re-issue the key.",
        )
    age = state.get("capture_age_seconds")
    if age is None or float(age) > TYPING_MAX_CAPTURE_AGE:
        return _deny(
            "stale_screenshot",
            "The last screenshot is stale; focus may have changed.",
            "Call computer_screenshot again before pressing keys.",
        )
    return _allow(verified=bool(letters))


# Re-exported so tools/tests can import the overview detector from one place.
__all__ = [
    "typing_preflight",
    "locate_typed_text",
    "check_landed",
    "keyboard_allowed",
    "detect_overview",
]
