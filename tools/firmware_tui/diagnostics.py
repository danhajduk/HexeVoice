from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
import re

from .models import Diagnostic, Severity


class DiagnosticParser(ABC):
    @abstractmethod
    def parse_line(self, line: str) -> Diagnostic | None:
        raise NotImplementedError


class GccDiagnosticParser(DiagnosticParser):
    _pattern = re.compile(
        r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
        r"(?P<severity>fatal error|error|warning|note):\s*(?P<message>.+)$"
    )

    def parse_line(self, line: str) -> Diagnostic | None:
        match = self._pattern.match(line.strip())
        if not match:
            return None
        raw_severity = match.group("severity")
        severity = Severity.ERROR if "error" in raw_severity else Severity(raw_severity)
        return Diagnostic(
            file=Path(match.group("file")),
            line=int(match.group("line")),
            column=int(match.group("column")) if match.group("column") else None,
            severity=severity,
            message=match.group("message"),
            raw=line.rstrip(),
        )


class CMakeDiagnosticParser(DiagnosticParser):
    _pattern = re.compile(r"^CMake\s+(?P<severity>Error|Warning)(?:\s+at\s+(?P<file>.+?):(?P<line>\d+))?.*?:\s*(?P<message>.+)$")

    def parse_line(self, line: str) -> Diagnostic | None:
        match = self._pattern.match(line.strip())
        if not match:
            return None
        return Diagnostic(
            file=Path(match.group("file")) if match.group("file") else None,
            line=int(match.group("line")) if match.group("line") else None,
            column=None,
            severity=Severity.ERROR if match.group("severity") == "Error" else Severity.WARNING,
            message=match.group("message"),
            raw=line.rstrip(),
        )


class CompositeDiagnosticParser(DiagnosticParser):
    def __init__(self, parsers: list[DiagnosticParser] | None = None) -> None:
        self.parsers = parsers or [GccDiagnosticParser(), CMakeDiagnosticParser()]

    def parse_line(self, line: str) -> Diagnostic | None:
        for parser in self.parsers:
            if diagnostic := parser.parse_line(line):
                return diagnostic
        return None
