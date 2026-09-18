from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class BuildAction(StrEnum):
    BUILD = "build"
    REBUILD = "rebuild"
    CLEAN = "clean"
    RUN = "run"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


@dataclass(slots=True)
class Diagnostic:
    file: Path | None
    line: int | None
    column: int | None
    severity: Severity
    message: str
    raw: str


@dataclass(slots=True)
class Target:
    name: str
    display_name: str
    idf_target: str
    partition_schema: str
    flash_size: str
    app_slot_size: str
    required_idf_version: str | None = None


@dataclass(slots=True)
class BuildProfile:
    name: str
    project_dir: Path
    compiler: Path
    target: str
    configuration: str = "Release"
    arguments: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    run_command: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CommandSpec:
    argv: list[str]
    cwd: Path
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class BuildResult:
    action: BuildAction
    command: CommandSpec
    duration_seconds: float
    exit_code: int
    diagnostics: list[Diagnostic]
    cancelled: bool = False

    @property
    def warning_count(self) -> int:
        return sum(item.severity == Severity.WARNING for item in self.diagnostics)

    @property
    def error_count(self) -> int:
        return sum(item.severity == Severity.ERROR for item in self.diagnostics)
