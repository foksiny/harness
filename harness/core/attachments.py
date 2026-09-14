"""
Media attachment support for Harness.

Detects image / video file paths inside user input — whether typed, pasted,
dropped ("swiped"), or given as a ``file://`` URI or ``data:`` URI — and turns
them into structured multimodal content blocks. Each provider then serializes
those blocks into its native vision format (OpenAI ``image_url``, Gemini
``inline_data``, Anthropic ``image``/``video`` blocks, NVIDIA NIM
``input_video``, and so on).

Canonical block shapes (kept small so they survive session persistence):

    {"type": "text",  "text": "..."}
    {"type": "image", "path": "/abs/path.png"}
    {"type": "video", "path": "/abs/path.mp4"}
    {"type": "image", "data_uri": "data:image/png;base64,..."}
"""
import base64
import os
import re
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple

IMAGE_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".apng",
})
VIDEO_EXTS = frozenset({
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".mpg", ".mpeg", ".ts",
})
# Inline base64 cap protects both the request payload and the session transcript.
MAX_INLINE_BYTES = 20 * 1024 * 1024

# Extra tokens that may be glued onto a filename when drag/drop pastes keep the
# full spaced path unquoted (e.g. ``My photo.png``).
_MAX_PATH_JOINS = 6

_MIME_TABLE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".avif": "image/avif",
    ".apng": "image/apng",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".m4v": "video/x-m4v",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".ts": "video/mp2t",
}

_DATA_URI_RE = re.compile(r"^data:([a-z0-9.+-]+/[a-z0-9.+-]+);base64,")


def media_kind_for_path(path: str) -> Optional[str]:
    """Return ``image`` / ``video`` for a recognized media extension, else None."""
    ext = os.path.splitext(path or "")[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def mime_type_for(path: str) -> str:
    return _MIME_TABLE.get(os.path.splitext(path or "")[1].lower(), "application/octet-stream")


def _media_candidate(token: str, cwd: str) -> Optional[Dict[str, Any]]:
    """Return a media block if ``token`` looks like an existing image/video."""
    tok = token.strip().strip("\"'")
    if not tok:
        return None

    if tok.startswith("data:") and ";base64," in tok:
        m = _DATA_URI_RE.match(tok)
        if m:
            kind = "video" if m.group(1).startswith("video/") else "image"
            return {"type": kind, "data_uri": tok}
        return None

    if tok.startswith("file://"):
        path = urllib.parse.unquote(tok[len("file://"):])
    else:
        path = tok

    # Strip trailing sentence punctuation (e.g. ``shot.png,``) when that leaves
    # a valid media file path.
    candidates = [path]
    stripped = path.rstrip(".,;:)}]>")
    if stripped and stripped != path:
        candidates.append(stripped)

    for cand in candidates:
        full = os.path.expanduser(cand)
        if not os.path.isabs(full):
            full = os.path.join(cwd, full)
        kind = media_kind_for_path(full)
        if kind and os.path.isfile(full):
            return {"type": kind, "path": os.path.abspath(full)}
    return None


def _tokenize(text: str) -> List[Tuple[int, int, str]]:
    """Split into whitespace-delimited (start, end, raw) runs, honoring quotes."""
    tokens: List[Tuple[int, int, str]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        start = i
        if text[i] in "\"'":
            quote = text[i]
            j = text.find(quote, i + 1)
            end = n if j == -1 else j + 1
        else:
            j = i
            while j < n and not text[j].isspace():
                j += 1
            end = j
        tokens.append((start, end, text[start:end]))
        i = end
    return tokens


def parse_attachments(
    text: str,
    cwd: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """Scan ``text`` for media file references.

    Returns a 3-tuple:
      * ``clean_text``  — the input with recognized media tokens removed,
      * ``blocks``      — canonical ``{"type": "image"|"video", ...}`` blocks,
      * ``warnings``    — human-readable notes for files that could not attach.
    """
    if not text:
        return text, [], []
    cwd = cwd or os.getcwd()
    tokens = _tokenize(text)
    blocks: List[Dict[str, Any]] = []
    spans: List[Tuple[int, int]] = []
    warnings: List[str] = []
    i = 0
    while i < len(tokens):
        start, end, raw = tokens[i]
        blk = _media_candidate(raw, cwd)
        if blk is None:
            # Drag/drop may paste an unquoted path containing spaces: greedily
            # glue following tokens until the joined span resolves to a file.
            for j in range(i + 1, min(len(tokens), i + 1 + _MAX_PATH_JOINS)):
                joined = _media_candidate(text[start:tokens[j][1]], cwd)
                if joined:
                    blk = joined
                    end = tokens[j][1]
                    i = j
                    break
        if blk is not None:
            if blk.get("path") and os.path.getsize(blk["path"]) > MAX_INLINE_BYTES:
                warnings.append(f"{blk['type']} '{blk['path']}' is over {MAX_INLINE_BYTES // (1024 * 1024)}MB and was not attached")
            else:
                blocks.append(blk)
                spans.append((start, end))
        i += 1

    if not spans:
        return text, [], warnings

    clean_text = _remove_spans(text, spans)
    uniq, seen = [], set()
    for b in blocks:
        key = b.get("path") or b.get("data_uri")
        if key not in seen:
            seen.add(key)
            uniq.append(b)
    return clean_text, uniq, warnings


def _remove_spans(text: str, spans: List[Tuple[int, int]]) -> str:
    parts: List[str] = []
    cursor = 0
    for start, end in sorted(spans):
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def data_url_for_block(block: Dict[str, Any]) -> Optional[str]:
    """data: URI for a block, or None when the file can't be inlined."""
    if block.get("data_uri"):
        return block["data_uri"]
    path = block.get("path")
    if not path or not os.path.isfile(path):
        return None
    try:
        if os.path.getsize(path) > MAX_INLINE_BYTES:
            return None
        with open(path, "rb") as f:
            payload = f.read()
    except OSError:
        return None
    b64 = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type_for(path)};base64,{b64}"


def b64_payload_for_block(block: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """(mime, base64) for a block, or (None, None) when it can't be inlined."""
    if block.get("data_uri"):
        m = _DATA_URI_RE.match(block["data_uri"])
        if not m:
            return None, None
        return m.group(1), block["data_uri"].split(",", 1)[1]
    path = block.get("path")
    if not path or not os.path.isfile(path):
        return None, None
    try:
        if os.path.getsize(path) > MAX_INLINE_BYTES:
            return None, None
        with open(path, "rb") as f:
            payload = f.read()
    except OSError:
        return None, None
    return mime_type_for(path), base64.b64encode(payload).decode("ascii")