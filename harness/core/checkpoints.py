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
    action: str  # "append", "truncate", "replace", "span_replace"
    index: int
    old_message: Optional[Dict[str, Any]] = None
    new_message: Optional[Dict[str, Any]] = None
    count: int = 1  # for truncate
    removed_messages: List[Dict[str, Any]] = field(default_factory=list)  # for truncate
    old_messages: List[Dict[str, Any]] = field(default_factory=list)  # for span_replace
    new_messages: List[Dict[str, Any]] = field(default_factory=list)  # for span_replace
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
        self._applicators: Dict[str, Callable] = {}
        self._last_apply_results: Dict[str, Any] = {}
        self._last_direction: str = "redo"
        self._lock = __import__("threading").RLock()
        self._session_id: Optional[str] = None
        
    def add_change_listener(self, listener: Callable):
        """Add a listener for change events (for UI updates)."""
        self._change_listeners.append(listener)
    
    def _notify_change(self):
        for listener in self._change_listeners:
            listener()
    
    def bind_session(self, session_id: Optional[str]):
        """Bind manager to a session for persistence. Loads existing checkpoints."""
        with self._lock:
            self._session_id = session_id
            if session_id:
                # Try to load existing checkpoints for this session
                try:
                    if not self.load(session_id):
                        # No existing checkpoints for this session - start fresh
                        self.checkpoints.clear()
                        self.current_index = -1
                        self._pending_changes = {
                            "file_changes": [],
                            "message_changes": [],
                            "state_changes": [],
                        }
                except Exception:
                    self.checkpoints.clear()
                    self.current_index = -1
                    self._pending_changes = {
                        "file_changes": [],
                        "message_changes": [],
                        "state_changes": [],
                    }
            else:
                self.clear()

    def _auto_save(self):
        """Persist current checkpoints if bound to a session."""
        if self._session_id:
            try:
                self.save(self._session_id)
            except Exception:
                pass

    # ============================================================
    # Change Recording (called by tools/agent when changes happen)
    # ============================================================
    
    def record_file_create(self, path: str, content: str):
        """Record a file creation."""
        with self._lock:
            self._pending_changes["file_changes"].append(FileChange(
                path=path,
                action="create",
                new_content=content,
            ))
    
    def record_file_edit(self, path: str, old_content: str, new_content: str):
        """Record a file edit with diff."""
        diff = self._compute_diff(old_content, new_content)
        with self._lock:
            self._pending_changes["file_changes"].append(FileChange(
                path=path,
                action="edit",
                old_content=old_content,
                new_content=new_content,
                diff=diff,
            ))
    
    def record_file_delete(self, path: str, old_content: str):
        """Record a file deletion."""
        with self._lock:
            self._pending_changes["file_changes"].append(FileChange(
                path=path,
                action="delete",
                old_content=old_content,
            ))
    
    def record_message_append(self, index: int, message: Dict[str, Any]):
        """Record a message append."""
        with self._lock:
            self._pending_changes["message_changes"].append(MessageChange(
                action="append",
                index=index,
                new_message=message,
            ))
    
    def record_message_truncate(self, start_index: int, count: int, removed_messages: List[Dict[str, Any]]):
        """Record a message truncation (e.g., compaction) as one atomic change."""
        with self._lock:
            self._pending_changes["message_changes"].append(MessageChange(
                action="truncate",
                index=start_index,
                count=count,
                removed_messages=list(removed_messages),
            ))
    
    def record_message_replace(self, index: int, old_message: Dict[str, Any], new_message: Dict[str, Any]):
        """Record a message replacement."""
        with self._lock:
            self._pending_changes["message_changes"].append(MessageChange(
                action="replace",
                index=index,
                old_message=old_message,
                new_message=new_message,
            ))
    
    def record_message_replace_span(self, index: int, old_messages: List[Dict[str, Any]], new_messages: List[Dict[str, Any]]):
        """Record an atomic swap of a contiguous span of messages (used for
        graduated compaction and mid-turn emergency trimming). Undo restores the
        old span; redo re-applies the new span."""
        with self._lock:
            self._pending_changes["message_changes"].append(MessageChange(
                action="span_replace",
                index=index,
                old_messages=list(old_messages),
                new_messages=list(new_messages),
            ))
    
    def record_state_change(self, key: str, old_value: Any, new_value: Any, action: str = "set"):
        """Record a state change (todos, config, etc.)."""
        with self._lock:
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
        with self._lock:
            if not any(self._pending_changes.values()):
                # No changes since last checkpoint - don't create duplicate
                return None
            
            checkpoint = Checkpoint(
                id=f"cp_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
                label=label or f"Checkpoint {len(self.checkpoints) + 1}",
                timestamp=time.time(),
                file_changes=list(self._pending_changes["file_changes"]),
                message_changes=list(self._pending_changes["message_changes"]),
                state_changes=list(self._pending_changes["state_changes"]),
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
            self._auto_save()
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
        """Undo to the previous checkpoint, reversing the current checkpoint's changes.
        Returns the checkpoint we're reverting TO.
        """
        with self._lock:
            # If there are pending un-checkpointed changes, auto-save them first
            # so they are not lost - user can redo to get them back.
            if any(self._pending_changes.values()):
                self.create_checkpoint("auto-save before undo")
            if not self.can_undo():
                return None
            
            # Reverse the changes captured in the checkpoint we are leaving.
            self._apply_checkpoint(self.checkpoints[self.current_index], undo=True)
            self._last_direction = "undo"
            self.current_index -= 1
            self._notify_change()
            self._auto_save()
            return self.checkpoints[self.current_index] if self.current_index >= 0 else None
    
    def redo(self) -> Optional[Checkpoint]:
        """Redo to the next checkpoint, re-applying its changes.
        Returns the checkpoint we're reverting TO.
        """
        with self._lock:
            if not self.can_redo():
                return None
            
            self._apply_checkpoint(self.checkpoints[self.current_index + 1], undo=False)
            self._last_direction = "redo"
            self.current_index += 1
            self._notify_change()
            self._auto_save()
            return self.checkpoints[self.current_index]
    
    def find_checkpoint_index(self, checkpoint_id: str) -> Optional[int]:
        """Locate a checkpoint by id, returning its index or None."""
        for i, cp in enumerate(self.checkpoints):
            if cp.id == checkpoint_id:
                return i
        return None
    
    def navigate_to(self, checkpoint_id: str) -> tuple:
        """Move the current pointer to the named checkpoint, applying reversals
        or redo changes as needed. Returns ``(checkpoint, moved)`` or ``(None, False)``
        when the checkpoint id does not exist.
        """
        target_index = self.find_checkpoint_index(checkpoint_id)
        if target_index is None:
            return None, False
        if target_index == self.current_index:
            return self.checkpoints[target_index], False
        
        if target_index > self.current_index:
            while self.current_index < target_index:
                self.redo()
        else:
            while self.current_index > target_index:
                self.undo()
        return self.checkpoints[target_index], True
    
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
    
    def set_applicators(self, *, apply_file_change: Callable[[FileChange, bool], bool],
                        apply_message_change: Callable[[MessageChange, bool], bool],
                        apply_state_change: Callable[[StateChange, bool], bool]) -> None:
        """Register the callbacks used to materialize undo/redo on live state.

        Each callback receives the change and an ``undo`` flag: ``undo=True`` means
        reverse the change, ``undo=False`` means re-apply it forward.
        """
        self._applicators = {
            "apply_file_change": apply_file_change,
            "apply_message_change": apply_message_change,
            "apply_state_change": apply_state_change,
        }
    
    def _apply_checkpoint(self, checkpoint: Checkpoint, undo: bool) -> Dict[str, Any]:
        """Reverse (undo) or re-apply (redo) a checkpoint's recorded changes.

        Undo iterates in reverse order; redo iterates in forward order. If no
        applicators were registered, changes are not materialized but the pointer
        movement still happens (legacy degrade).
        """
        results = {
            "files": 0,
            "messages": 0,
            "states": 0,
            "errors": [],
        }
        applicators = getattr(self, "_applicators", None)
        if not applicators:
            self._last_apply_results = results
            return results
        
        f_apply = applicators["apply_file_change"]
        m_apply = applicators["apply_message_change"]
        s_apply = applicators["apply_state_change"]
        
        changes = list(checkpoint.file_changes)
        if undo:
            changes.reverse()
        for fc in changes:
            try:
                if f_apply(fc, undo):
                    results["files"] += 1
                else:
                    results["errors"].append(f"Failed to {'undo' if undo else 'redo'} file change: {fc.path}")
            except Exception as e:
                results["errors"].append(f"Error applying file change {fc.path}: {e}")
        
        changes = list(checkpoint.message_changes)
        if undo:
            changes.reverse()
        for mc in changes:
            try:
                if m_apply(mc, undo):
                    results["messages"] += 1
                else:
                    results["errors"].append(f"Failed to {'undo' if undo else 'redo'} message change at index {mc.index}")
            except Exception as e:
                results["errors"].append(f"Error applying message change: {e}")
        
        changes = list(checkpoint.state_changes)
        if undo:
            changes.reverse()
        for sc in changes:
            try:
                if s_apply(sc, undo):
                    results["states"] += 1
                else:
                    results["errors"].append(f"Failed to {'undo' if undo else 'redo'} state change: {sc.key}")
            except Exception as e:
                results["errors"].append(f"Error applying state change: {e}")
        
        self._last_apply_results = results
        return results
    
    def last_apply_results(self) -> Dict[str, Any]:
        """Summary of the most recent undo/redo materialization."""
        return dict(getattr(self, "_last_apply_results", None) or {})
    
    def last_direction(self) -> str:
        """Direction of the most recent pointer move: 'undo' or 'redo'."""
        return getattr(self, "_last_direction", "redo")
    
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
            clean_id = session_id.replace("/", "_").replace("\\", "_")
            file_path = self.storage_dir / f"{clean_id}_checkpoints.json"
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
            clean_id = session_id.replace("/", "_").replace("\\", "_")
            file_path = self.storage_dir / f"{clean_id}_checkpoints.json"
            if not file_path.exists():
                # Try fuzzy match for legacy files
                for p in self.storage_dir.glob("*.json"):
                    if session_id in p.stem:
                        file_path = p
                        break
                else:
                    return False
                if not file_path.exists():
                    return False
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            with self._lock:
                self.checkpoints = [self._dict_to_checkpoint(d) for d in data.get("checkpoints", [])]
                self.current_index = data.get("current_index", -1)
                self._pending_changes = {
                    "file_changes": [],
                    "message_changes": [],
                    "state_changes": [],
                }
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
        with self._lock:
            self.checkpoints.clear()
            self.current_index = -1
            self._pending_changes = {
                "file_changes": [],
                "message_changes": [],
                "state_changes": [],
            }
            self._notify_change()
            self._auto_save()


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