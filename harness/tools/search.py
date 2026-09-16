"""
Search tools for Harness.
Implements blazing-fast ripgrep / regex code searching across project files.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
from harness.tools.base import Tool
from harness.core.context_budget import DEFAULT_OUTPUT_LIMITS, truncate_output

_GREP_MAX_CHARS = int(DEFAULT_OUTPUT_LIMITS.get("grep_search", 8000))

def find_rg_binary() -> Optional[str]:
    """Locate rg in PATH or ~/.cargo/bin."""
    bin_path = shutil.which("rg")
    if bin_path:
        return bin_path
    cargo_rg = Path.home() / ".cargo" / "bin" / "rg"
    if cargo_rg.exists() and os.access(cargo_rg, os.X_OK):
        return str(cargo_rg)
    return None

class GrepSearchTool(Tool):
    name = "grep_search"
    description = "Search for a regex or text query across project files using ripgrep or python fallback."
    action_type = "search"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text or regex pattern to search for."},
            "path": {"type": "string", "description": "Directory or file to search within (default: '.')."},
            "case_sensitive": {"type": "boolean", "description": "Case-sensitive search (default: false)."},
            "max_results": {"type": "integer", "description": "Maximum number of results to return (default: 50)."},
            "max_chars": {"type": "integer", "description": "Max chars of output to return (default ~8000)."},
        },
        "required": ["query"],
    }

    def execute(self, query: str, path: str = ".", case_sensitive: bool = False, max_results: int = 50, max_chars: Optional[int] = None, **kwargs) -> str:
        target = Path(path).expanduser().resolve()
        rg_bin = find_rg_binary()

        if rg_bin:
            cmd = [rg_bin, "--line-number", "--max-count", str(max_results), "--heading"]
            if not case_sensitive:
                cmd.append("-i")
            cmd.extend(["--", query, str(target)])

            try:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
                if proc.stdout.strip():
                    lines = proc.stdout.strip().split("\n")
                    if len(lines) > max_results * 2:
                        lines = lines[:max_results * 2]
                        lines.append(f"... (truncated at {max_results} results)")
                    return self._limit("\n".join(lines), max_chars)
                return f"No matches found for '{query}' in '{path}'."
            except Exception:
                pass # Fallback to python search

        # Python regex fallback
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(query, flags)
            matches = []

            for root, dirs, files in os.walk(target):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", ".git", "venv", ".venv", "__pycache__")]
                for f in files:
                    if f.startswith("."):
                        continue
                    fpath = Path(root) / f
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as file:
                            for i, line in enumerate(file, 1):
                                if regex.search(line):
                                    rel = os.path.relpath(fpath, os.getcwd())
                                    matches.append(f"{rel}:{i}: {line.strip()[:160]}")
                                    if len(matches) >= max_results:
                                        break
                    except Exception:
                        continue
                if len(matches) >= max_results:
                    break

            if not matches:
                return f"No matches found for '{query}' in '{path}'."
            return self._limit(f"Found {len(matches)} match(es):\n" + "\n".join(matches), max_chars)
        except Exception as ex:
            return f"Error executing search: {str(ex)}"

    def _limit(self, text: str, max_chars: Optional[int] = None) -> str:
        cap = _GREP_MAX_CHARS if max_chars is None else int(max_chars)
        if cap > 0 and len(text) > cap:
            return truncate_output(text, cap, marker_note=(
                f"...[output truncated by context budget: was {len(text)} chars, "
                f"keeping first {cap}]... (pass max_chars={len(text)} to this "
                f"grep_search call for the full result)"
            ))
        return text
