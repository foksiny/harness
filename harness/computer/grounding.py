"""Grounding helpers (D3): 0-1000 normalized coords + Set-of-Marks notes.

No grounding model is bundled (keeps the lightweight constraint). Marking
degrades gracefully: AX/text snapshot if available, else raw 0-1000 coords
scaled by capture geometry.
"""
from typing import Dict, Any, List


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
    ny = int(round(max(0.0, min(float(h) - 1, y)) / h * 1000.0)) if h > 1 else 0
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
            "nx": nx["x"], "ny": nx["y"],
            "selector_hint": el.get("selector_hint", ""),
        })
    return marks


def render_marks_text(marks: List[Dict[str, Any]]) -> str:
    if not marks:
        return "No marks (no elements detected; fall back to AX/text snapshot or 0-1000 coords)."
    lines = ["Set-of-Marks (click by mark number; coords are 0-1000 normalized):"]
    for m in marks[:50]:
        lines.append(f"[{m['mark']}] {m['role']} '{m['name']}' ref={m['ref']} nx={m['nx']} ny={m['ny']} hint={m['selector_hint']}")
    return "\n".join(lines)
