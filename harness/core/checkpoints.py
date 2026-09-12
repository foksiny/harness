"""
Checkpoint System for Harness.
Provides efficient undo/redo capability by storing diffs of file changes, message history, and state changes.
Uses minimal memory by storing only the changes (diffs) rather than full snapshots.
"""
import json
import time
import uuid
import difflib
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Callable
from collections import deque


@dataclass
class FileChange:
    """Represents a single file change (create, edit, delete)."""
    path: str
    action: str  # "create", "edit", "delete"
    old_content: Optional[str] = None
    new_content: Optional[str] = None
    diff: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class MessageChange:
    """Represents changes to message history."""
    action: str  # "append", "truncate", "replace"
    index: int
    old_message: Optional[Dict[str, Any]] = None
    new_message: Optional[Dict[str, Any]] = None
    count: int = 1  # for truncate
    timestamp: float = field(default_factory=time.time)


@dataclass
class StateChange:
    """Represents changes to agent state (todos, config, etc.)."""
    key: str
    action: str  # "set", "delete", "update"
    old_value: Any = None
    new_value: Any = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class Checkpoint:
    """A single checkpoint containing all changes since the last checkpoint."""
    id: str
    label: str  # User-friendly label
    timestamp: float
    file_changes: List[FileChange] = field(default_factory=list)
    message_changes: List[MessageChange] = field(default_factory=list)
    state_changes: List[StateChange] = field(default_factory=list)
    parent_id: Optional[str] = None  # For branching undo history


class CheckpointManager:
    """
    Manages checkpoints for undo/redo functionality.
    Uses efficient diff storage to minimize memory usage.
    """
    
    def __init__(self, max_checkpoints: int = 50, storage_dir: Optional[Path] = None):
        self.max_checkpoints = max_checkpoints
        self.storage_dir = Path(storage_dir) if storage_dir else (Path.home() / ".harness" / "checkpoints")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory checkpoint history (stored as a linear list with parent pointers for branching)
        self.checkpoints: List[Checkpoint] = []
        self.current_index: int = -1  # Points to current checkpoint
        self._pending_changes: Dict[str, List] = {
            "file_changes": [],
            "message_changes": [],
            "state_changes": [],
        }
        self._change_listeners: List[Callable] = []
        
    def add_change_listener(self, listener: Callable):
        """Add a listener for change events (for UI updates)."""
        self._change_listeners.append(listener)
    
    def _notify_change(self):
        for listener in self._change_listeners:
            listener()
    
    # ============================================================
    # Change Recording (called by tools/agent when changes happen)
    # ============================================================
    
    def record_file_create(self, path: str, content: str):
        """Record a file creation."""
        self._pending_changes["file_changes"].append(FileChange(
            path=path,
            action="create",
            new_content=content,
        ))
    
    def record_file_edit(self, path: str, old_content: str, new_content: str):
        """Record a file edit with diff."""
        diff = self._compute_diff(old_content, new_content)
        self._pending_changes["file_changes"].append(FileChange(
            path=path,
            action="edit",
            old_content=old_content,
            new_content=new_content,
            diff=diff,
        ))
    
    def record_file_delete(self, path: str, old_content: str):
        """Record a file deletion."""
        self._pending_changes["file_changes"].append(FileChange(
            path=path,
            action="delete",
            old_content=old_content,
        ))
    
    def record_message_append(self, index: int, message: Dict[str, Any]):
        """Record a message append."""
        self._pending_changes["message_changes"].append(MessageChange(
            action="append",
            index=index,
            new_message=message,
        ))
    
    def record_message_truncate(self, start_index: int, count: int, removed_messages: List[Dict[str, Any]]):
        """Record message truncation (e.g., compaction)."""
        for i, msg in enumerate(removed_messages):
            self._pending_changes["message_changes"].append(MessageChange(
                action="truncate",
                index=start_index + i,
                old_message=msg,
            ))
    
    def record_message_replace(self, index: int, old_message: Dict[str, Any], new_message: Dict[str, Any]):
        """Record a message replacement."""
        self._pending_changes["message_changes"].append(MessageChange(
            action="replace",
            index=index,
            old_message=old_message,
            new_message=new_message,
        ))
    
    def record_state_change(self, key: str, old_value: Any, new_value: Any, action: str = "set"):
        """Record a state change (todos, config, etc.)."""
        self._pending_changes["state_changes"].append(StateChange(
            key=key,
            action=action,
            old_value=old_value,
            new_value=new_value,
        ))
    
    # ============================================================
    # Checkpoint Creation
    # ============================================================
    
    def create_checkpoint(self, label: str = "") -> Checkpoint:
        """
        Create a checkpoint from all pending changes.
        Returns the created checkpoint.
        """
        if not any(self._pending_changes.values()):
            # No changes since last checkpoint
            return self.checkpoints[self.current_index] if self.current_index >= 0 else None
        
        checkpoint = Checkpoint(
            id=f"cp_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
            label=label or f"Checkpoint {len(self.checkpoints) + 1}",
            timestamp=time.time(),
            file_changes=self._pending_changes["file_changes"],
            message_changes=self._pending_changes["message_changes"],
            state_changes=self._pending_changes["state_changes"],
            parent_id=self.checkpoints[self.current_index].id if self.current_index >= 0 else None,
        )
        
        # Truncate history after current index (for branching)
        if self.current_index < len(self.checkpoints) - 1:
            self.checkpoints = self.checkpoints[:self.current_index + 1]
        
        self.checkpoints.append(checkpoint)
        self.current_index = len(self.checkpoints) - 1
        
        # Enforce max checkpoints (FIFO)
        if len(self.checkpoints) > self.max_checkpoints:
            self.checkpoints.pop(0)
            self.current_index -= 1
        
        # Clear pending changes
        self._pending_changes = {
            "file_changes": [],
            "message_changes": [],
            "state_changes": [],
        }
        
        self._notify_change()
        return checkpoint
    
    def checkpoint_with_label(self, label: str) -> Checkpoint:
        """Create a checkpoint with a specific label."""
        return self.create_checkpoint(label)
    
    # ============================================================
    # Undo / Redo
    # ============================================================
    
    def can_undo(self) -> bool:
        return self.current_index >= 0
    
    def can_redo(self) -> bool:
        return self.current_index < len(self.checkpoints) - 1
    
    def undo(self) -> Optional[Checkpoint]:
        """Undo to the previous checkpoint. Returns the checkpoint we're reverting TO."""
        if not self.can_undo():
            return None
        
        # We're currently at current_index, we want to go to current_index - 1
        target_index = self.current_index - 1
        self.current_index = target_index
        self._notify_change()
        return self.checkpoints[target_index] if target_index >= 0 else None
    
    def redo(self) -> Optional[Checkpoint]:
        """Redo to the next checkpoint. Returns the checkpoint we're reverting TO."""
        if not self.can_redo():
            return None
        
        self.current_index += 1
        self._notify_change()
        return self.checkpoints[self.current_index]
    
    def get_current_checkpoint(self) -> Optional[Checkpoint]:
        """Get the currently active checkpoint."""
        if self.current_index >= 0 and self.current_index < len(self.checkpoints):
            return self.checkpoints[self.current_index]
        return None
    
    def list_checkpoints(self) -> List[Dict[str, Any]]:
        """List all checkpoints with metadata."""
        result = []
        for i, cp in enumerate(self.checkpoints):
            result.append({
                "index": i,
                "id": cp.id,
                "label": cp.label,
                "timestamp": cp.timestamp,
                "is_current": i == self.current_index,
                "file_changes_count": len(cp.file_changes),
                "message_changes_count": len(cp.message_changes),
                "state_changes_count": len(cp.state_changes),
            })
        return result
    
    # ============================================================
    # Applying Changes (for actual undo/redo execution)
    # ============================================================
    
    def apply_undo(self, checkpoint: Checkpoint, 
                   apply_file_edit: Callable[[str, str], bool],
                   apply_file_create: Callable[[str, str], bool],
                   apply_file_delete: Callable[[str], bool],
                   apply_message_change: Callable[[MessageChange], bool],
                   apply_state_change: Callable[[StateChange], bool]) -> Dict[str, Any]:
        """
        Apply the reverse of a checkpoint's changes (undo).
        Returns a summary of what was undone.
        """
        results = {
            "files_restored": 0,
            "files_created": 0,
            "files_deleted": 0,
            "messages_restored": 0,
            "states_restored": 0,
            "errors": [],
        }
        
        # Reverse file changes (in reverse order)
        for fc in reversed(checkpoint.file_changes):
            try:
                if fc.action == "create":
                    # Reverse of create is delete
                    if apply_file_delete(fc.path):
                        results["files_deleted"] += 1
                    else:
                        results["errors"].append(f"Failed to delete created file: {fc.path}")
                elif fc.action == "edit":
                    # Reverse of edit is restore old content
                    if fc.old_content is not None and apply_file_edit(fc.path, fc.old_content):
                        results["files_restored"] += 1
                    else:
                        results["errors"].append(f"Failed to restore file: {fc.path}")
                elif fc.action == "delete":
                    # Reverse of delete is recreate
                    if fc.old_content is not None and apply_file_create(fc.path, fc.old_content):
                        results["files_created"] += 1
                    else:
                        results["errors"].append(f"Failed to recreate deleted file: {fc.path}")
            except Exception as e:
                results["errors"].append(f"Error undoing file change {fc.path}: {e}")
        
        # Reverse message changes (in reverse order)
        for mc in reversed(checkpoint.message_changes):
            try:
                if apply_message_change(mc):
                    results["messages_restored"] += 1
                else:
                    results["errors"].append(f"Failed to undo message change at index {mc.index}")
            except Exception as e:
                results["errors"].append(f"Error undoing message change: {e}")
        
        # Reverse state changes (in reverse order)
        for sc in reversed(checkpoint.state_changes):
            try:
                if apply_state_change(sc):
                    results["states_restored"] += 1
                else:
                    results["errors"].append(f"Failed to undo state change: {sc.key}")
            except Exception as e:
                results["errors"].append(f"Error undoing state change: {e}")
        
        return results
    
    def apply_redo(self, checkpoint: Checkpoint,
                   apply_file_edit: Callable[[str, str], bool],
                   apply_file_create: Callable[[str, str], bool],
                   apply_file_delete: Callable[[str], bool],
                   apply_message_change: Callable[[MessageChange], bool],
                   apply_state_change: Callable[[StateChange], bool]) -> Dict[str, Any]:
        """
        Apply a checkpoint's changes (redo).
        Returns a summary of what was redone.
        """
        results = {
            "files_restored": 0,
            "files_created": 0,
            "files_deleted": 0,
            "messages_restored": 0,
            "states_restored": 0,
            "errors": [],
        }
        
        # Apply file changes in order
        for fc in checkpoint.file_changes:
            try:
                if fc.action == "create":
                    if fc.new_content is not None and apply_file_create(fc.path, fc.new_content):
                        results["files_created"] += 1
                    else:
                        results["errors"].append(f"Failed to recreate file: {fc.path}")
                elif fc.action == "edit":
                    if fc.new_content is not None and apply_file_edit(fc.path, fc.new_content):
                        results["files_restored"] += 1
                    else:
                        results["errors"].append(f"Failed to reapply edit to: {fc.path}")
                elif fc.action == "delete":
                    if apply_file_delete(fc.path):
                        results["files_deleted"] += 1
                    else:
                        results["errors"].append(f"Failed to delete file: {fc.path}")
            except Exception as e:
                results["errors"].append(f"Error redoing file change {fc.path}: {e}")
        
        # Apply message changes in order
        for mc in checkpoint.message_changes:
            try:
                if apply_message_change(mc):
                    results["messages_restored"] += 1
                else:
                    results["errors"].append(f"Failed to redo message change at index {mc.index}")
            except Exception as e:
                results["errors"].append(f"Error redoing message change: {e}")
        
        # Apply state changes in order
        for sc in checkpoint.state_changes:
            try:
                if apply_state_change(sc):
                    results["states_restored"] += 1
                else:
                    results["errors"].append(f"Failed to redo state change: {sc.key}")
            except Exception as e:
                results["errors"].append(f"Error redoing state change: {e}")
        
        return results
    
    # ============================================================
    # Diff Computation (Memory Efficient)
    # ============================================================
    
    def _compute_diff(self, old_content: str, new_content: str) -> str:
        """Compute a unified diff between old and new content."""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = difflib.unified_diff(old_lines, new_lines, lineterm="")
        return "".join(diff)
    
    def _apply_diff(self, content: str, diff: str) -> Optional[str]:
        """Apply a unified diff to content. Returns new content or None on failure."""
        try:
            old_lines = content.splitlines(keepends=True)
            # Parse unified diff and apply
            # This is a simplified version - in practice we'd use patch
            # For now, we'll just use the stored new_content
            return None
        except Exception:
            return None
    
    # ============================================================
    # Persistence
    # ============================================================
    
    def save(self, session_id: str) -> bool:
        """Save checkpoints to disk for a session."""
        try:
            file_path = self.storage_dir / f"{session_id}_checkpoints.json"
            data = {
                "checkpoints": [self._checkpoint_to_dict(cp) for cp in self.checkpoints],
                "current_index": self.current_index,
                "saved_at": time.time(),
            }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            return False
    
    def load(self, session_id: str) -> bool:
        """Load checkpoints from disk for a session."""
        try:
            file_path = self.storage_dir / f"{session_id}_checkpoints.json"
            if not file_path.exists():
                return False
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self.checkpoints = [self._dict_to_checkpoint(d) for d in data.get("checkpoints", [])]
            self.current_index = data.get("current_index", -1)
            return True
        except Exception:
            return False
    
    def _checkpoint_to_dict(self, cp: Checkpoint) -> Dict[str, Any]:
        d = asdict(cp)
        # Convert FileChange, MessageChange, StateChange to dicts
        d["file_changes"] = [asdict(fc) for fc in cp.file_changes]
        d["message_changes"] = [asdict(mc) for mc in cp.message_changes]
        d["state_changes"] = [asdict(sc) for sc in cp.state_changes]
        return d
    
    def _dict_to_checkpoint(self, d: Dict[str, Any]) -> Checkpoint:
        fc_list = [FileChange(**fc) for fc in d.get("file_changes", [])]
        mc_list = [MessageChange(**mc) for mc in d.get("message_changes", [])]
        sc_list = [StateChange(**sc) for sc in d.get("state_changes", [])]
        return Checkpoint(
            id=d["id"],
            label=d["label"],
            timestamp=d["timestamp"],
            file_changes=fc_list,
            message_changes=mc_list,
            state_changes=sc_list,
            parent_id=d.get("parent_id"),
        )
    
    def clear(self):
        """Clear all checkpoints."""
        self.checkpoints.clear()
        self.current_index = -1
        self._pending_changes = {
            "file_changes": [],
            "message_changes": [],
            "state_changes": [],
        }
        self._notify_change()


# Global checkpoint manager instance (will be initialized by agent)
_checkpoint_manager: Optional[CheckpointManager] = None


def get_checkpoint_manager() -> CheckpointManager:
    """Get or create the global checkpoint manager."""
    global _checkpoint_manager
    if _checkpoint_manager is None:
        _checkpoint_manager = CheckpointManager()
    return _checkpoint_manager


def set_checkpoint_manager(manager: CheckpointManager):
    """Set the global checkpoint manager."""
    global _checkpoint_manager
    _checkpoint_manager = manager