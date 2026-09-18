"""
Harness Update Engine.
Supports updating from Git repository (with changelog preview and dependency sync)
or from PyPI / Pip package installations.
"""
import os
import sys
import subprocess
import json
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from harness import __version__


@dataclass
class UpdateInfo:
    is_git: bool
    current_version: str
    is_behind: bool
    commits_behind: int = 0
    commits: List[str] = field(default_factory=list)
    latest_version: Optional[str] = None
    repo_path: Optional[Path] = None
    branch: Optional[str] = None
    error: Optional[str] = None


@dataclass
class UpdateResult:
    success: bool
    message: str
    commits: List[str] = field(default_factory=list)
    previous_version: str = __version__
    new_version: str = __version__
    error: Optional[str] = None


class HarnessUpdater:
    """Manages update discovery, changelog retrieval, and execution for Harness."""

    def __init__(self, target_dir: Optional[Path] = None):
        if target_dir:
            self.repo_dir = Path(target_dir).resolve()
        else:
            # Look up from harness package directory: harness/core/updater.py -> root
            package_root = Path(__file__).resolve().parents[2]
            if (package_root / ".git").exists():
                self.repo_dir = package_root
            elif (Path.cwd() / ".git").exists() and (Path.cwd() / "harness").exists():
                self.repo_dir = Path.cwd()
            else:
                self.repo_dir = package_root

        self.is_git = (self.repo_dir / ".git").is_dir()

    def check_for_updates(self, timeout: int = 20) -> UpdateInfo:
        """Check upstream repository or PyPI for available updates."""
        if self.is_git:
            return self._check_git(timeout=timeout)
        return self._check_pypi(timeout=timeout)

    def _check_git(self, timeout: int = 20) -> UpdateInfo:
        try:
            # 1. Detect current branch
            branch = (
                subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=str(self.repo_dir),
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=5,
                ).strip()
                or "main"
            )

            # 2. Fetch origin
            subprocess.check_output(
                ["git", "fetch", "origin"],
                cwd=str(self.repo_dir),
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )

            # 3. Upstream target ref
            upstream = f"origin/{branch}"

            # Check if upstream ref exists
            try:
                subprocess.check_output(
                    ["git", "rev-parse", "--verify", upstream],
                    cwd=str(self.repo_dir),
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except Exception:
                upstream = "@{u}"

            # 4. Count commits behind
            count_str = subprocess.check_output(
                ["git", "rev-list", "--count", f"HEAD..{upstream}"],
                cwd=str(self.repo_dir),
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            ).strip()
            commits_behind = int(count_str) if count_str.isdigit() else 0

            # 5. Get commit summary changelog
            commits = []
            if commits_behind > 0:
                log_out = subprocess.check_output(
                    ["git", "log", f"HEAD..{upstream}", "--oneline", "-n", "8"],
                    cwd=str(self.repo_dir),
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=5,
                )
                commits = [line.strip() for line in log_out.splitlines() if line.strip()]

            return UpdateInfo(
                is_git=True,
                current_version=__version__,
                is_behind=(commits_behind > 0),
                commits_behind=commits_behind,
                commits=commits,
                repo_path=self.repo_dir,
                branch=branch,
            )
        except Exception as ex:
            return UpdateInfo(
                is_git=True,
                current_version=__version__,
                is_behind=False,
                error=str(ex),
                repo_path=self.repo_dir,
            )

    def _check_pypi(self, timeout: int = 5) -> UpdateInfo:
        try:
            req = urllib.request.Request(
                "https://pypi.org/pypi/harness-cli/json",
                headers={"User-Agent": f"Harness/{__version__}"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                latest = data.get("info", {}).get("version", __version__)
                is_behind = latest != __version__
                return UpdateInfo(
                    is_git=False,
                    current_version=__version__,
                    is_behind=is_behind,
                    latest_version=latest,
                )
        except Exception as ex:
            return UpdateInfo(
                is_git=False,
                current_version=__version__,
                is_behind=False,
                error=str(ex),
            )

    def apply_update(self, force: bool = False, timeout: int = 60) -> UpdateResult:
        """Execute update by pulling git changes or running pip upgrade."""
        if self.is_git:
            return self._apply_git(force=force, timeout=timeout)
        return self._apply_pypi(timeout=timeout)

    def _apply_git(self, force: bool = False, timeout: int = 60) -> UpdateResult:
        info = self._check_git(timeout=30)
        if not info.is_behind:
            return UpdateResult(
                success=True,
                message="Already up to date!",
                previous_version=__version__,
                new_version=__version__,
            )

        # Check dirty tree
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(self.repo_dir),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()

        stashed = False
        if status_out:
            if not force:
                return UpdateResult(
                    success=False,
                    message="Working directory has local modifications.",
                    error="Uncommitted changes in repository. Use --force to stash changes and proceed.",
                    previous_version=__version__,
                    new_version=__version__,
                )
            # Stash changes
            subprocess.run(
                ["git", "stash", "push", "-m", "harness-auto-stash"],
                cwd=str(self.repo_dir),
                capture_output=True,
                timeout=10,
            )
            stashed = True

        # Pull
        pull_res = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(self.repo_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if pull_res.returncode != 0:
            # Fallback to standard pull
            pull_res = subprocess.run(
                ["git", "pull"],
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        if pull_res.returncode != 0:
            if stashed:
                subprocess.run(["git", "stash", "pop"], cwd=str(self.repo_dir), capture_output=True)
            return UpdateResult(
                success=False,
                message="Git pull failed.",
                error=pull_res.stderr.strip() or pull_res.stdout.strip(),
                previous_version=__version__,
                new_version=__version__,
            )

        # Restore stashed changes if needed
        if stashed:
            subprocess.run(["git", "stash", "pop"], cwd=str(self.repo_dir), capture_output=True)

        # Read new version
        new_version = __version__
        try:
            from harness import __version__ as _nv
            new_version = _nv
        except Exception:
            pass

        # Reinstall package in editable mode if pip is available
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", "."],
                cwd=str(self.repo_dir),
                capture_output=True,
                timeout=45,
            )
        except Exception:
            pass

        return UpdateResult(
            success=True,
            message=f"Successfully updated ({info.commits_behind} commits pulled)!",
            commits=info.commits,
            previous_version=__version__,
            new_version=new_version,
        )

    def _apply_pypi(self, timeout: int = 60) -> UpdateResult:
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "harness-cli"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if res.returncode == 0:
            return UpdateResult(
                success=True,
                message="Successfully updated via pip!",
                previous_version=__version__,
                new_version="latest",
            )
        return UpdateResult(
            success=False,
            message="Pip upgrade failed.",
            error=res.stderr.strip(),
            previous_version=__version__,
            new_version=__version__,
        )
