"""
Filesystem tools for Harness.
Supports high-precision file viewing, atomic line replacement, creation, and tree listing.
"""
import os
import glob
from pathlib import Path
from typing import Dict, Any, Optional
from harness.tools.base import Tool
from harness.core.checkpoints import get_checkpoint_manager

class ViewFileTool(Tool):
    name = "view_file"
    description = "Read file contents with line numbering and optional start_line/end_line slice boundaries."
    action_type = "read_file"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative or absolute path to the file."},
            "start_line": {"type": "integer", "description": "1-indexed starting line number (optional)."},
            "end_line": {"type": "integer", "description": "1-indexed ending line number (optional)."},
        },
        "required": ["path"],
    }

    def execute(self, path: str, start_line: Optional[int] = None, end_line: Optional[int] = None, **kwargs) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: File '{path}' does not exist."
        if p.is_dir():
            return f"Error: '{path}' is a directory, not a file. Use list_dir instead."

        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            total_lines = len(lines)
            s = max(1, start_line or 1)
            e = min(total_lines, end_line or total_lines)

            if s > total_lines:
                return f"Error: start_line {s} exceeds total lines ({total_lines})."

            output_lines = []
            for i in range(s, e + 1):
                output_lines.append(f"{i:5d} | {lines[i - 1].rstrip()}")

            header = f"--- [{p.name}] Lines {s}-{e} of {total_lines} ---"
            return header + "\n" + "\n".join(output_lines)
        except Exception as ex:
            return f"Error reading file '{path}': {str(ex)}"

class EditFileTool(Tool):
    name = "edit_file"
    description = "Perform a precise atomic replacement of target_content with replacement_content in an existing file."
    action_type = "edit_file"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to edit."},
            "target_content": {"type": "string", "description": "The exact string block to replace."},
            "replacement_content": {"type": "string", "description": "The replacement content to insert."},
        },
        "required": ["path", "target_content", "replacement_content"],
    }

    def execute(self, path: str, target_content: str, replacement_content: str, **kwargs) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: File '{path}' does not exist. Use write_file to create new files."

        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()

            if target_content not in content:
                # Provide helpful debugging context
                return (
                    f"Error: target_content not found in '{path}'. "
                    f"Ensure exact match including whitespace and indentation."
                )

            count = content.count(target_content)
            if count > 1:
                return (
                    f"Error: target_content appears {count} times in '{path}'. "
                    f"Please include more surrounding context to ensure a unique match."
                )

            new_content = content.replace(target_content, replacement_content, 1)
            with open(p, "w", encoding="utf-8") as f:
                f.write(new_content)
            
            # Record change for checkpoint
            cp_manager = get_checkpoint_manager()
            cp_manager.record_file_edit(path, content, new_content)

            return f"Successfully updated '{path}' (1 replacement applied)."
        except Exception as ex:
            return f"Error editing file '{path}': {str(ex)}"

class WriteFileTool(Tool):
    name = "write_file"
    description = "Create a new file or overwrite an existing file with complete content."
    action_type = "write_file"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write."},
            "content": {"type": "string", "description": "Full file content."},
            "overwrite": {"type": "boolean", "description": "Whether to overwrite if file already exists (default: true)."},
        },
        "required": ["path", "content"],
    }

    def execute(self, path: str, content: str, overwrite: bool = True, **kwargs) -> str:
        p = Path(path).expanduser().resolve()
        if p.exists() and not overwrite:
            return f"Error: File '{path}' already exists and overwrite is set to False."

        try:
            old_content = None
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    old_content = f.read()
            
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            
            # Record change for checkpoint
            cp_manager = get_checkpoint_manager()
            if old_content is not None:
                cp_manager.record_file_edit(path, old_content, content)
            else:
                cp_manager.record_file_create(path, content)

            return f"Successfully wrote {len(content)} characters to '{path}'."
        except Exception as ex:
            return f"Error writing file '{path}': {str(ex)}"

class ListDirTool(Tool):
    name = "list_dir"
    description = "List entries in a directory with file sizes and type indicators."
    action_type = "list_dir"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to inspect (default: '.')."},
            "max_depth": {"type": "integer", "description": "Maximum recursive depth (default: 2)."},
        },
    }

    def execute(self, path: str = ".", max_depth: int = 2, **kwargs) -> str:
        root = Path(path).expanduser().resolve()
        if not root.exists():
            return f"Error: Directory '{path}' does not exist."
        if not root.is_dir():
            return f"Error: '{path}' is a file, not a directory."

        results = []
        try:
            for current, dirs, files in os.walk(root):
                rel = os.path.relpath(current, root)
                depth = 0 if rel == "." else rel.count(os.sep) + 1
                if depth > max_depth:
                    dirs.clear()
                    continue

                # Filter hidden / heavy directories
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv", "target", "dist", "build")]

                indent = "  " * depth
                if rel != ".":
                    results.append(f"{indent}📁 {os.path.basename(current)}/")

                for f in sorted(files):
                    if f.startswith("."):
                        continue
                    fpath = Path(current) / f
                    size = fpath.stat().st_size if fpath.exists() else 0
                    results.append(f"{indent}  📄 {f} ({size} B)")

            if not results:
                return f"Directory '{path}' is empty."
            return f"Contents of {root.name}/:\n" + "\n".join(results[:150])
        except Exception as ex:
            return f"Error listing directory '{path}': {str(ex)}"

class FindFilesTool(Tool):
    name = "find_files"
    description = "Search for files matching a glob pattern (e.g. '*.py', '**/*.json')."
    action_type = "search"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. '*.py', 'src/**/*.ts')."},
            "path": {"type": "string", "description": "Directory to search from (default: '.')."},
        },
        "required": ["pattern"],
    }

    def execute(self, pattern: str, path: str = ".", **kwargs) -> str:
        root = Path(path).expanduser().resolve()
        search_pattern = str(root / pattern)
        matches = glob.glob(search_pattern, recursive=True)
        if not matches:
            return f"No files matching '{pattern}' in '{path}'."

        rel_paths = []
        for m in sorted(matches)[:100]:
            try:
                rel = os.path.relpath(m, os.getcwd())
            except ValueError:
                rel = m
            rel_paths.append(rel)

        return f"Found {len(matches)} match(es):\n" + "\n".join(f"- {p}" for p in rel_paths)


class DeleteFileTool(Tool):
    name = "delete_file"
    description = "Delete a file."
    action_type = "delete_file"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to delete."},
        },
        "required": ["path"],
    }

    def execute(self, path: str, **kwargs) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: File '{path}' does not exist."
        if p.is_dir():
            return f"Error: '{path}' is a directory, not a file."

        try:
            # Read content before deleting for checkpoint
            with open(p, "r", encoding="utf-8") as f:
                old_content = f.read()
            
            p.unlink()
            
            # Record change for checkpoint
            cp_manager = get_checkpoint_manager()
            cp_manager.record_file_delete(path, old_content)

            return f"Successfully deleted '{path}'."
        except Exception as ex:
            return f"Error deleting file '{path}': {str(ex)}"
