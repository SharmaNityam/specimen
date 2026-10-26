"""Stable interfaces shared by the engine, CLI, server, and UI."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class RunConfig:
    input_path: Path
    script_path: Path
    output_dir: Path
    sensitive_columns: list[str] = field(default_factory=list)
    runner: Literal["local", "docker"] = "docker"
    max_runs: int = 200
    timeout: float = 3.0
    max_seconds: float = 120.0
    seed: int = 42
    # Optional, validated suggestions. No source data needs to leave the machine.
    policy: dict[str, Any] = field(default_factory=dict)


@dataclass
class Execution:
    returncode: int
    fingerprint: str | None
    exception_type: str | None
    location: str | None
    timed_out: bool = False
    duration_ms: int = 0
    # Internal diagnostics: never export raw stdout/stderr by default.
    stdout: str = ""
    stderr: str = ""

