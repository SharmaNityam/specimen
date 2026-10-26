"""Subprocess runners for specimen reduction.

Diagnostics are deliberately reduced to a traceback fingerprint. Captured output is
bounded and is kept only in the returned Execution object for internal callers.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import tempfile
import uuid
from pathlib import Path

from .contracts import Execution

_OUTPUT_LIMIT = 64 * 1024
_DOCKER_IMAGE = "python:3.12-slim"
_FRAME_RE = re.compile(r'^\s*File "(.*)", line (\d+), in (.+)$')
_EXCEPTION_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*)(?::|$)")
_INFRA_EXCEPTIONS = {
    "SyntaxError", "IndentationError", "TabError", "ImportError",
    "ModuleNotFoundError", "FileNotFoundError", "PermissionError",
    "RunnerInfrastructureError", "TimeoutError",
}


def _drain(pipe, sink: bytearray) -> None:
    while True:
        block = pipe.read(8192)
        if not block:
            break
        if len(sink) < _OUTPUT_LIMIT:
            sink.extend(block[: _OUTPUT_LIMIT - len(sink)])


def _terminate_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, AttributeError):
        try:
            proc.terminate()
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=0.02)
    except subprocess.TimeoutExpired:
        pass
    # The leader may exit on SIGTERM while a child remains in the same group.
    # Always escalate for the group, regardless of the leader's wait result.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    proc.wait()


def _cleanup_descendants(proc: subprocess.Popen) -> None:
    """Stop children left behind after the Python script's leader exits."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, AttributeError):
        return
    time.sleep(0.01)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError):
        pass


def _fingerprint(stderr: str, script_path: Path) -> tuple[str | None, str | None, str | None]:
    """Return exception type, safe location, and stable fingerprint."""
    lines = stderr.splitlines()
    if not any("Traceback (most recent call last):" in line for line in lines):
        return None, None, None
    frames: list[tuple[str, str]] = []
    frame_line_indices: list[int] = []
    target = str(script_path.resolve())
    for line_index, line in enumerate(lines):
        match = _FRAME_RE.match(line)
        if match:
            filename, lineno, function = match.groups()
            frame_line_indices.append(line_index)
            try:
                is_script = Path(filename).resolve() == Path(target)
            except (OSError, RuntimeError):
                is_script = False
            if is_script:
                frames.append((lineno, function))
    # The exception record immediately follows the final traceback frame and
    # its indented source excerpt. Exception messages can span multiple lines;
    # searching backward would mistake a later message word for a class name.
    exception_type = None
    start = frame_line_indices[-1] + 1 if frame_line_indices else 0
    for line in lines[start:]:
        if not line.strip() or line[:1].isspace():
            continue
        match = _EXCEPTION_RE.match(line)
        if match:
            exception_type = match.group(1).rsplit(".", 1)[-1]
            break
        if "Exception Group Traceback" in line:
            continue
    if not exception_type:
        return None, None, None
    if frames:
        lineno, function = frames[-1]
        location = f"line {lineno} in {function}"
    else:
        # No paths, source excerpts, or exception messages are included.
        location = "no script frame"
    return exception_type, location, f"{exception_type}|{location}"


def _execute(command: list[str], script_path: Path, timeout: float, cwd: Path | None = None) -> Execution:
    started = time.monotonic()
    out, err = bytearray(), bytearray()
    try:
        proc = subprocess.Popen(
            command, cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        # Never include the host exception string: it can contain paths or values.
        return Execution(127, None, "RunnerInfrastructureError", "runner unavailable",
                         duration_ms=int((time.monotonic() - started) * 1000),
                         stderr=type(exc).__name__)

    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, err), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        proc.wait(timeout=max(0.0001, timeout))
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_group(proc)
    else:
        _cleanup_descendants(proc)
    for thread in threads:
        thread.join(timeout=1.0)
    stdout = out.decode("utf-8", "replace")
    stderr = err.decode("utf-8", "replace")
    elapsed = int((time.monotonic() - started) * 1000)
    if timed_out:
        return Execution(proc.returncode if proc.returncode is not None else -signal.SIGKILL,
                         None, "TimeoutError", "execution timeout", True, elapsed, stdout, stderr)
    exc_type, location, fingerprint = _fingerprint(stderr, script_path)
    return Execution(proc.returncode or 0, fingerprint, exc_type, location, False, elapsed, stdout, stderr)


def run_script(script: Path, csv_path: Path, runner: str = "docker", timeout: float = 3.0) -> Execution:
    """Run a Python script against a CSV using Docker or explicitly trusted local mode."""
    script = Path(script).resolve()
    csv_path = Path(csv_path).resolve()
    if runner == "local":
        return _execute([sys.executable, str(script), str(csv_path)], script, timeout, script.parent)
    if runner != "docker":
        return Execution(2, None, "RunnerInfrastructureError", "unknown runner")
    docker = shutil.which("docker")
    if not docker:
        return Execution(127, None, "RunnerInfrastructureError", "Docker executable unavailable")
    with tempfile.TemporaryDirectory(prefix="specimen-docker-") as stage_dir:
        stage = Path(stage_dir)
        staged_script = stage / "script.py"
        staged_csv = stage / "input.csv"
        try:
            shutil.copyfile(script, staged_script)
            shutil.copyfile(csv_path, staged_csv)
            staged_script.chmod(0o644)
            staged_csv.chmod(0o644)
        except OSError:
            return Execution(2, None, "RunnerInfrastructureError", "Docker input staging failed")
        command = [
            docker, "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "--memory", "256m", "--cpus", "0.5", "--pids-limit", "64",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "--mount", f"type=bind,src={staged_script},dst=/work/script.py,readonly",
            "--mount", f"type=bind,src={staged_csv},dst=/input/input.csv,readonly",
            _DOCKER_IMAGE, "python", "/work/script.py", "/input/input.csv",
        ]
        container_name = f"specimen-{uuid.uuid4().hex[:16]}"
        command[2:2] = ["--name", container_name]
        result = _execute(command, Path("/work/script.py"), timeout)
        if result.timed_out:
            # Killing the Docker CLI does not guarantee it removed a timed-out
            # container. Ask the daemon before deleting the staged mounts.
            try:
                subprocess.run([docker, "rm", "-f", container_name], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=2.0, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if result.returncode and not result.fingerprint and not result.timed_out:
            # Docker daemon, image pull, mount and runtime errors are infrastructure.
            result.exception_type = "RunnerInfrastructureError"
            result.location = "Docker execution unavailable"
        return result


def is_infrastructure(execution: Execution) -> bool:
    return execution.timed_out or execution.exception_type in _INFRA_EXCEPTIONS or execution.exception_type == "RunnerInfrastructureError"
