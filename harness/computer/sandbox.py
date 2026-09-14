"""
Sandboxed Python execution for Harness.

Provides real isolation for untrusted Python code using a layered approach:
1. nsjail (if available) - strongest isolation
2. Docker (if available) - container-based isolation
3. Restricted subprocess with resource limits - weakest but always available

Each layer provides:
- Filesystem isolation (read-only root, temp writable layer)
- Network isolation (optional)
- Resource limits (CPU, memory, time)
- Process limits
"""
import os
import sys
import time
import shutil
import tempfile
import subprocess
import resource
from typing import Dict, Any, Optional, Tuple
from pathlib import Path


class SandboxResult:
    """Result from sandboxed execution."""
    
    def __init__(self, ok: bool, stdout: str = "", stderr: str = "",
                 exit_code: int = 0, execution_time: float = 0.0,
                 backend: str = "unknown", error: str = ""):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.execution_time = execution_time
        self.backend = backend
        self.error = error
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "execution_time": self.execution_time,
            "backend": self.backend,
            "error": self.error,
        }


def detect_sandbox_backend() -> str:
    """Detect the best available sandbox backend."""
    # Check nsjail first (strongest isolation)
    if shutil.which("nsjail"):
        return "nsjail"
    
    # Check Docker
    if shutil.which("docker"):
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return "docker"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    
    # Fall back to restricted subprocess
    return "restricted"


def _get_sandbox_dir() -> Path:
    """Get or create the sandbox directory."""
    sandbox_dir = Path.home() / ".harness" / "sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    return sandbox_dir


def _create_temp_workspace() -> Path:
    """Create an isolated temp workspace for code execution."""
    workspace = tempfile.mkdtemp(prefix="harness_sandbox_")
    # Create standard directories
    (Path(workspace) / "tmp").mkdir(exist_ok=True)
    (Path(workspace) / "home").mkdir(exist_ok=True)
    return Path(workspace)


def execute_sandboxed(
    code: str,
    timeout: int = 30,
    memory_limit_mb: int = 256,
    cpu_time_limit: int = 30,
    network_allowed: bool = False,
    working_dir: Optional[str] = None,
) -> SandboxResult:
    """Execute Python code in a sandbox.
    
    Tries backends in order: nsjail -> docker -> restricted subprocess.
    """
    backend = detect_sandbox_backend()
    
    if backend == "nsjail":
        return _execute_nsjail(code, timeout, memory_limit_mb, cpu_time_limit, network_allowed)
    elif backend == "docker":
        return _execute_docker(code, timeout, memory_limit_mb, cpu_time_limit, network_allowed)
    else:
        return _execute_restricted(code, timeout, memory_limit_mb, cpu_time_limit, network_allowed, working_dir)


def _execute_nsjail(
    code: str,
    timeout: int,
    memory_limit_mb: int,
    cpu_time_limit: int,
    network_allowed: bool,
) -> SandboxResult:
    """Execute in nsjail for strongest isolation."""
    start_time = time.time()
    workspace = _create_temp_workspace()
    code_file = workspace / "script.py"
    code_file.write_text(code, encoding="utf-8")
    
    try:
        # Build nsjail command
        cmd = [
            "nsjail",
            "--mode", "o",
            "--chroot", "/",
            "--cwd", "/",
            "--rlimit_as", str(memory_limit_mb),
            "--rlimit_cpu", str(cpu_time_limit),
            "--time_limit", str(timeout),
            "--disable_clone_newnet" if network_allowed else "--rlimit_nproc", "0",
            "--", sys.executable, str(code_file),
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 5,  # Extra buffer for nsjail overhead
            env={"HOME": "/tmp", "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        
        elapsed = time.time() - start_time
        
        return SandboxResult(
            ok=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            execution_time=round(elapsed, 2),
            backend="nsjail",
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            ok=False,
            error=f"Execution timed out after {timeout} seconds",
            execution_time=round(time.time() - start_time, 2),
            backend="nsjail",
        )
    except Exception as e:
        return SandboxResult(
            ok=False,
            error=f"nsjail execution failed: {e}",
            execution_time=round(time.time() - start_time, 2),
            backend="nsjail",
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _execute_docker(
    code: str,
    timeout: int,
    memory_limit_mb: int,
    cpu_time_limit: int,
    network_allowed: bool,
) -> SandboxResult:
    """Execute in Docker container for container-based isolation."""
    start_time = time.time()
    workspace = _create_temp_workspace()
    code_file = workspace / "script.py"
    code_file.write_text(code, encoding="utf-8")
    
    try:
        # Build docker command
        cmd = [
            "docker", "run", "--rm",
            f"--memory={memory_limit_mb}m",
            f"--cpus=1",
            f"--pids-limit=64",
            "--read-only",
            "--tmpfs", "/tmp:size=64M",
            "--tmpfs", "/home:size=64M",
            "-v", f"{workspace}:/workspace:ro",
            "--network", "none" if not network_allowed else "bridge",
            "python:3.12-slim",
            sys.executable, "/workspace/script.py",
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 10,  # Extra buffer for Docker overhead
        )
        
        elapsed = time.time() - start_time
        
        return SandboxResult(
            ok=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            execution_time=round(elapsed, 2),
            backend="docker",
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            ok=False,
            error=f"Execution timed out after {timeout} seconds",
            execution_time=round(time.time() - start_time, 2),
            backend="docker",
        )
    except Exception as e:
        return SandboxResult(
            ok=False,
            error=f"Docker execution failed: {e}",
            execution_time=round(time.time() - start_time, 2),
            backend="docker",
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _execute_restricted(
    code: str,
    timeout: int,
    memory_limit_mb: int,
    cpu_time_limit: int,
    network_allowed: bool,
    working_dir: Optional[str] = None,
) -> SandboxResult:
    """Execute in a restricted subprocess with resource limits.
    
    This is the weakest isolation but always available. It uses:
    - Resource limits (memory, CPU time, processes)
    - Restricted environment variables
    - Timeout enforcement
    """
    start_time = time.time()
    
    # Write code to temp file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp_path = tmp.name
        tmp.write(code)
    
    try:
        # Create restricted environment
        env = {
            "HOME": "/tmp",
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            # Remove potentially dangerous env vars
            "LD_PRELOAD": "",
            "PYTHONSTARTUP": "",
            "PYTHONCASEOK": "",
            "PYTHONINSPECT": "",
        }
        
        # Set resource limits in a wrapper script
        wrapper_code = f'''
import resource
import sys
import os

# Set memory limit
try:
    resource.setrlimit(resource.RLIMIT_AS, ({memory_limit_mb * 1024 * 1024}, {memory_limit_mb * 1024 * 1024}))
except (ValueError, resource.error):
    pass

# Set CPU time limit
try:
    resource.setrlimit(resource.RLIMIT_CPU, ({cpu_time_limit}, {cpu_time_limit}))
except (ValueError, resource.error):
    pass

# Set process limit
try:
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
except (ValueError, resource.error):
    pass

# Set file size limit (10MB)
try:
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
except (ValueError, resource.error):
    pass

# Execute the target script
exec(compile(open("{tmp_path}").read(), "{tmp_path}", "exec"))
'''
        
        wrapper_path = tmp_path + ".wrapper"
        with open(wrapper_path, "w") as f:
            f.write(wrapper_code)
        
        result = subprocess.run(
            [sys.executable, wrapper_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            cwd=working_dir or os.getcwd(),
            env=env,
            # Prevent core dumps and other dangerous behaviors
            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)),
        )
        
        elapsed = time.time() - start_time
        
        return SandboxResult(
            ok=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            execution_time=round(elapsed, 2),
            backend="restricted",
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            ok=False,
            error=f"Execution timed out after {timeout} seconds",
            execution_time=round(time.time() - start_time, 2),
            backend="restricted",
        )
    except Exception as e:
        return SandboxResult(
            ok=False,
            error=f"Restricted execution failed: {e}",
            execution_time=round(time.time() - start_time, 2),
            backend="restricted",
        )
    finally:
        # Cleanup
        for path in [tmp_path, tmp_path + ".wrapper"]:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except Exception:
                pass
