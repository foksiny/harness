"""Grounding helpers: 0-1000 normalized coords + OCR-derived Set-of-Marks.

No grounding model is bundled (keeps the lightweight constraint). Marks come
from tesseract's TSV output (line-level bounding boxes), so both vision and
non-vision models get the same clickable, stable identifiers instead of having
to guess pixels. When tesseract is missing, everything degrades to plain
0-1000 normalized coordinates.
"""
from __future__ import annotations

import os
import re
import subprocess
import unicodedata
from typing import Any, Dict, List, Optional, Union


def normalize_coords(x: float, y: float, width: int, height: int) -> Dict[str, int]:
    """Convert 0-1000 normalized coords to absolute pixels (clamped)."""
    w, h = max(1, width), max(1, height)
    ax = int(round(max(0.0, min(1000.0, x)) / 1000.0 * w))
    ay = int(round(max(0.0, min(1000.0, y)) / 1000.0 * h))
    return {"x": min(ax, w - 1), "y": min(ay, h - 1)}


def denormalize_coords(x: float, y: float, width: int, height: int) -> Dict[str, int]:
    """Convert absolute pixels to 0-1000 normalized coords."""
    w, h = max(1, width), max(1, height)
    nx = int(round(max(0.0, min(float(w) - 1, x)) / w * 1000.0)) if w > 1 else 0
    ny = int(round(max(0.0, min(float(h) - 1, y)) / h * 1000.0)) if w > 1 else 0
    return {"x": nx, "y": ny}


def annotate_marks(elements: List[Dict[str, Any]], width: int, height: int) -> List[Dict[str, Any]]:
    """Build mark metadata for an annotated screenshot.

    ``elements``: [{role, name, x, y, ref}] with absolute coords.
    Returns numbered marks with normalized coords + predicted selector hints.
    """
    marks = []
    for i, el in enumerate(elements, 1):
        nx = denormalize_coords(el.get("x", 0), el.get("y", 0), width, height)
        marks.append({
            "mark": i,
            "role": el.get("role", "element"),
            "name": el.get("name", ""),
            "ref": el.get("ref", f"mark_{i}"),
            "x": int(el.get("x", 0)), "y": int(el.get("y", 0)),
            "nx": nx["x"], "ny": nx["y"],
            "selector_hint": el.get("selector_hint", ""),
        })
    return marks


def parse_ocr_tsv(
    tsv: str,
    width: int = 0,
    height: int = 0,
    max_marks: int = 70,
    min_confidence: float = 35.0,
) -> List[Dict[str, Any]]:
    """Group tesseract TSV words into line-level marks with coordinates.

    Line-level marks are the sweet spot for UI automation: precise enough to
    click a username/button, stable enough to avoid 40 separate word marks.
    When ``width``/``height`` are omitted, the page row (level 1) supplies the
    image dimensions, so no image library is required.
    """
    if not tsv:
        return []
    rows = tsv.splitlines()
    if not rows:
        return []
    header = rows[0].split("\t")
    try:
        idx = {name: header.index(name) for name in (
            "level", "page_num", "block_num", "par_num", "line_num",
            "word_num", "left", "top", "width", "height", "conf", "text",
        )}
    except ValueError:
        return []

    lines: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []
    page_width = int(width or 0)
    page_height = int(height or 0)
    for row in rows[1:]:
        cols = row.split("\t")
        if len(cols) <= idx["text"]:
            continue
        try:
            level = int(cols[idx["level"]])
        except ValueError:
            continue
        if level == 1:
            # Tesseract's page row carries the real image dimensions.
            try:
                page_width = page_width or int(cols[idx["width"]])
                page_height = page_height or int(cols[idx["height"]])
            except ValueError:
                pass
            continue
        if level != 5:
            continue
        text = (cols[idx["text"]] or "").strip()
        if not text:
            continue
        try:
            conf = float(cols[idx["conf"]])
        except ValueError:
            continue
        if conf < min_confidence:
            continue
        try:
            left = int(cols[idx["left"]])
            top = int(cols[idx["top"]])
            w = int(cols[idx["width"]])
            h = int(cols[idx["height"]])
        except ValueError:
            continue
        key = (cols[idx["page_num"]], cols[idx["block_num"]],
               cols[idx["par_num"]], cols[idx["line_num"]])
        if key not in lines:
            lines[key] = {
                "left": left, "top": top, "right": left + w, "bottom": top + h,
                "words": [],
            }
            order.append(key)
        entry = lines[key]
        entry["left"] = min(entry["left"], left)
        entry["top"] = min(entry["top"], top)
        entry["right"] = max(entry["right"], left + w)
        entry["bottom"] = max(entry["bottom"], top + h)
        entry["words"].append((int(cols[idx["word_num"]]), text))

    width, height = page_width, page_height
    if width <= 1 or height <= 1:
        return []

    marks: List[Dict[str, Any]] = []
    for key in order:
        entry = lines[key]
        text = " ".join(w for _, w in sorted(entry["words"]))
        if not text:
            continue
        left = max(0, entry["left"])
        top = max(0, entry["top"])
        right = min(width, entry["right"])
        bottom = min(height, entry["bottom"])
        cx = max(0, min(width - 1, (left + right) // 2))
        cy = max(0, min(height - 1, (top + bottom) // 2))
        nx = denormalize_coords(cx, cy, width, height)
        marks.append({
            "mark": len(marks) + 1,
            "role": "text",
            "name": text[:80],
            "text": text[:80],
            "ref": f"ocr_line_{len(marks) + 1}",
            "x": cx, "y": cy,
            "w": right - left, "h": bottom - top,
            "nx": nx["x"], "ny": nx["y"],
        })
        if len(marks) >= max_marks:
            break
    return marks


def ocr_marks(
    image_path: str,
    max_marks: int = 70,
    min_confidence: float = 35.0,
) -> List[Dict[str, Any]]:
    """Run tesseract TSV on ``image_path`` and return line-level marks."""
    if not image_path or not os.path.isfile(image_path):
        return []
    try:
        proc = subprocess.run(
            ["tesseract", image_path, "stdout", "tsv"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return parse_ocr_tsv(proc.stdout, max_marks=max_marks,
                         min_confidence=min_confidence)


def find_mark(marks: List[Dict[str, Any]], number: Any) -> Optional[Dict[str, Any]]:
    """Return the mark whose ``mark`` field equals ``number`` (int or str)."""
    try:
        wanted = int(number)
    except (TypeError, ValueError):
        return None
    for mark in marks or []:
        try:
            if int(mark.get("mark")) == wanted:
                return mark
        except (TypeError, ValueError):
            continue
    return None


def render_marks_text(marks: List[Dict[str, Any]], max_marks: int = 50) -> str:
    """Human/model-readable mark list. Empty string when no marks exist."""
    if not marks:
        return ""
    lines = [
        "Set-of-Marks (click with computer_click mark=N; coords are 0-1000 normalized):"
    ]
    for m in marks[:max_marks]:
        text = str(m.get("name") or m.get("text") or "").strip()
        lines.append(
            f"[{m['mark']}] '{text}' nx={m['nx']} ny={m['ny']}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GUI guards: overview detection + delivery verification
# ---------------------------------------------------------------------------

def fold_text(s: Any) -> str:
    """Lowercase, accent-fold, collapse whitespace. For robust OCR matching."""
    if s is None:
        return ""
    text = unicodedata.normalize("NFKD", str(s))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


# GNOME/COSMIC overview + search placeholders. NOTE: generic "search" is
# deliberately NOT a marker — in-app search fields (Discord's "Encontre ou
# comece uma conversa", browser address bars) are legitimate typing targets.
# Only the compositor-level search is an overview, where typing + Return
# launches the top search hit (the Claude-instead-of-Discord failure).
_OVERVIEW_SEARCH_MARKERS = (
    "digite para pesquisar",
    "type to search",
)
# Corroborating overview chrome (needs both, to avoid false positives).
_OVERVIEW_CHROME_MARKERS = (
    ("activities", "atvidades"),
    ("show applications", "mostrar aplicativos", "show apps"),
)


def _coerce_text(marks_or_text: Union[str, List[Dict[str, Any]], None]) -> str:
    if marks_or_text is None:
        return ""
    if isinstance(marks_or_text, str):
        return marks_or_text
    parts: List[str] = []
    for m in marks_or_text or []:
        if isinstance(m, dict):
            parts.append(str(m.get("name") or m.get("text") or ""))
        else:
            parts.append(str(m))
    return "\n".join(parts)


def detect_overview(
    marks_or_text: Union[str, List[Dict[str, Any]], None]
) -> Dict[str, Any]:
    """Detect GNOME/COSMIC overview / app-search launcher on screen.

    Returns ``{"overview": bool, "reason": str}``. Never raises; empty input
    means "unknown, not overview" (we cannot prove it, so we do not block).
    """
    text = fold_text(_coerce_text(marks_or_text))
    if not text:
        return {"overview": False, "reason": ""}
    for marker in _OVERVIEW_SEARCH_MARKERS:
        if marker in text:
            return {
                "overview": True,
                "reason": (
                    "app-search/overview is open "
                    f"(saw '{marker}') — typing+Return here searches and "
                    "launches the top hit instead of reaching the app"
                ),
            }
    # Secondary path: Activities/Atividades + Show Applications together.
    left = any(m in text for m in _OVERVIEW_CHROME_MARKERS[0])
    right = any(m in text for m in _OVERVIEW_CHROME_MARKERS[1])
    if left and right:
        return {
            "overview": True,
            "reason": "overview chrome detected (Activities + Show Applications)",
        }
    return {"overview": False, "reason": ""}


_STOPWORDS = frozenset({
    "parte", "para", "com", "que", "dos", "das", "uma", "the", "and", "with",
    "this", "that", "from",
})


def distinctive_words(text: str, min_len: int = 5, max_words: int = 12) -> List[str]:
    """Long, non-stopword words — the parts OCR is most likely to catch."""
    words: List[str] = []
    seen = set()
    for w in re.split(r"[^a-z0-9]+", fold_text(text)):
        if len(w) < min_len or w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        words.append(w)
        if len(words) >= max_words:
            break
    return words


def typed_text_present(ocr_text: Any, typed_text: Any) -> bool:
    """Did the typed text land on screen? Tolerant to OCR mistakes.

    Exact folded-substring first (the common case: message visible in the
    input or in chat history). Falls back to distinctive-word overlap for
    long messages that wrap/truncate. Never raises.
    """
    try:
        typed = fold_text(typed_text)
        ocr = fold_text(ocr_text)
        if not typed or not ocr:
            return False
        if len(typed) <= 32:
            return typed in ocr
        # Long text: first 24 folded chars (catches the start of a sent row),
        # else at least 2 distinctive words from the whole text.
        head = typed[:24].strip()
        if head and head in ocr:
            return True
        words = distinctive_words(typed)
        hits = sum(1 for w in words if w in ocr)
        return hits >= 2
    except Exception:
        return False


def is_escape_key(key: Any) -> bool:
    """True for Escape/Esc in any common spelling (the overview bail-out)."""
    raw = str(key or "").strip().lower().replace("-", "_").replace(" ", "_")
    return raw.replace("_", "") in ("escape", "esc")


def is_escape_combo(keys: Any) -> bool:
    """True when a combo is exactly [Escape] (plausible overview exit)."""
    if keys is None:
        return False
    if isinstance(keys, str):
        keys = [keys]
    try:
        parts = [str(k).strip() for k in keys if str(k).strip()]
    except Exception:
        return False
    return len(parts) == 1 and is_escape_key(parts[0])
