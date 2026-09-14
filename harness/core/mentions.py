"""
File/folder @-mention expansion for Harness.

In user prompts, @/path/to/file or @/path/to/dir are replaced with a concise
summary so the model can work without loading the whole file.

- File: reports line count, char count, first 10 lines, then truncates.
- Folder: renders a limited directory tree.
"""
import os
import re
from pathlib import Path
from typing import List, Tuple

# Regex for a mention: @ followed by a non-whitespace sequence.
# We stop at punctuation that typically terminates a path.
MENTION_RE = re.compile(r'@([^\s]+)')

# Limits to keep the prompt manageable.
MAX_LINES_PREVIEW = 10
MAX_FOLDER_DEPTH = 2
MAX_ITEMS_PER_DIR = 80
MAX_TOTAL_FOLDER_LINES = 200

def _strip_trailing_punct(token: str) -> Tuple[str, str]:
    """Return (clean_path, trailing_punct) stripping punctuation like . , ; ) etc."""
    trailing = ""
    while token and token[-1] in ".,;:!?)}]>":
        trailing = token[-1] + trailing
        token = token[:-1]
    return token, trailing

def _summarize_file(path: Path) -> str:
    try:
        stat = path.stat()
    except Exception:
        return f"[@{path}] (unreadable: could not stat file)"
    try:
        # Try text decode as UTF-8; fallback to binary size notice
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            # Non-text file
            return (
                f"[@{path}] Binary file\n"
                f"- Size: {stat.st_size} bytes\n"
                f"- Lines/chars: N/A (binary)\n"
                f"- First bytes: {data[:80]!r}"
            )
    except Exception as exc:
        return f"[@{path}] (read error: {exc})"

    lines = text.splitlines()
    line_count = len(lines)
    char_count = len(text)
    preview = "\n".join(lines[:MAX_LINES_PREVIEW])
    more = ""
    if line_count > MAX_LINES_PREVIEW:
        more = f"\n... [truncated {line_count - MAX_LINES_PREVIEW} more lines]"
    return (
        f"[@{path}] File summary\n"
        f"- Lines: {line_count}\n"
        f"- Characters: {char_count}\n"
        f"- First {min(MAX_LINES_PREVIEW, line_count)} lines:\n"
        f"```\n{preview}{more}\n```"
    )

def _render_tree(root: Path, depth: int = 0, max_depth: int = MAX_FOLDER_DEPTH) -> str:
    if depth > max_depth:
        return ""
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except Exception:
        return f"{'  '*depth}{root.name}/ (unreadable)"
    lines: List[str] = []
    for i, entry in enumerate(entries):
        if i >= MAX_ITEMS_PER_DIR:
            lines.append(f"{'  '*depth}... ({len(list(root.iterdir())) - MAX_ITEMS_PER_DIR} more items omitted)")
            break
        if entry.is_dir():
            lines.append(f"{'  '*depth}{entry.name}/")
            if depth < max_depth:
                subtree = _render_tree(entry, depth + 1, max_depth)
                if subtree:
                    lines.append(subtree)
        else:
            try:
                size = entry.stat().st_size
            except Exception:
                size = 0
            lines.append(f"{'  '*depth}{entry.name} ({size} bytes)")
    return "\n".join(lines)

def _summarize_folder(path: Path) -> str:
    try:
        stat = path.stat()
    except Exception:
        return f"[@{path}] (unreadable: could not stat folder)"
    tree = _render_tree(path, depth=0, max_depth=MAX_FOLDER_DEPTH)
    return (
        f"[@{path}] Folder summary\n"
        f"- Entries: (showing up to {MAX_FOLDER_DEPTH} levels)\n"
        f"```\n{tree}\n```"
    )

def expand_mentions(text: str, cwd: str) -> Tuple[str, List[str]]:
    """
    Expand @mentions in `text`.

    Returns (expanded_text, warnings). Warnings are for mentions that could not be
    resolved.
    """
    if not text:
        return text, []
    cwd_path = Path(cwd).resolve()
    warnings: List[str] = []
    # We'll replace iteratively to avoid overlapping issues.
    # Use a function for re.sub replacement
    def replacer(match: re.Match) -> str:
        raw = match.group(1)
        clean_path, trailing = _strip_trailing_punct(raw)
        if not clean_path:
            return "@" + trailing
        # Resolve path relative to cwd
        candidate = Path(clean_path)
        if not candidate.is_absolute():
            candidate = cwd_path / candidate
        try:
            candidate = candidate.resolve(strict=True)
        except FileNotFoundError:
            warnings.append(f"@{clean_path} not found")
            return f"@{clean_path}{trailing}"
        except RuntimeError:
            # Path resolution loop etc.
            warnings.append(f"@{clean_path} could not be resolved")
            return f"@{clean_path}{trailing}"

        if candidate.is_file():
            summary = _summarize_file(candidate)
            return summary + trailing
        elif candidate.is_dir():
            summary = _summarize_folder(candidate)
            return summary + trailing
        else:
            warnings.append(f"@{clean_path} exists but is neither file nor dir")
            return f"@{clean_path}{trailing}"

    expanded = MENTION_RE.sub(replacer, text)
    return expanded, warnings
