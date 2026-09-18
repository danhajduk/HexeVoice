from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import os
from pathlib import Path
import shlex
import time

from .diagnostics import CompositeDiagnosticParser, DiagnosticParser
from .models import BuildAction, BuildProfile, BuildResult, CommandSpec, Diagnostic


OutputCallback = Callable[[str, bool], Awaitable[None] | None]


def escaped_command(argv: list[str]) -> str:
    return shlex.join(argv)


class FirmwareCompiler:
    def command_for(self, profile: BuildProfile, action: BuildAction) -> CommandSpec:
        if action == BuildAction.RUN:
            if not profile.run_command:
                raise ValueError("This profile has no run command configured")
            argv = [*profile.run_command]
        else:
            argv = [str(profile.compiler), "--profile", profile.target]
            if action == BuildAction.REBUILD:
                argv.append("--clean")
            elif action == BuildAction.CLEAN:
                argv.append("--clean-only")
            else:
                argv.append("--verbose")
            if action == BuildAction.REBUILD:
                argv.append("--verbose")
            argv.extend(profile.arguments)
        environment = {
            **profile.environment,
            "CMAKE_BUILD_TYPE": profile.configuration,
            "HEXE_BUILD_CONFIGURATION": profile.configuration,
        }
        return CommandSpec(argv=argv, cwd=profile.project_dir, environment=environment)


class AsyncBuildRunner:
    def __init__(self, parser: DiagnosticParser | None = None) -> None:
        self.parser = parser or CompositeDiagnosticParser()
        self.process: asyncio.subprocess.Process | None = None
        self._cancel_requested = False

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def cancel(self) -> None:
        process = self.process
        if process is None or process.returncode is not None:
            return
        self._cancel_requested = True
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def run(
        self,
        command: CommandSpec,
        action: BuildAction,
        on_output: OutputCallback | None = None,
    ) -> BuildResult:
        if self.running:
            raise RuntimeError("A build is already running")
        self._cancel_requested = False
        diagnostics: list[Diagnostic] = []
        started = time.monotonic()
        environment = os.environ.copy()
        environment.update(command.environment)
        self.process = await asyncio.create_subprocess_exec(
            *command.argv,
            cwd=command.cwd,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def consume(stream: asyncio.StreamReader | None, is_stderr: bool) -> None:
            if stream is None:
                return
            while chunk := await stream.readline():
                line = chunk.decode(errors="replace").rstrip("\r\n")
                if diagnostic := self.parser.parse_line(line):
                    diagnostics.append(diagnostic)
                if on_output:
                    callback_result = on_output(line, is_stderr)
                    if asyncio.iscoroutine(callback_result):
                        await callback_result

        try:
            await asyncio.gather(consume(self.process.stdout, False), consume(self.process.stderr, True))
            exit_code = await self.process.wait()
        finally:
            duration = time.monotonic() - started
            self.process = None
        return BuildResult(
            action=action,
            command=command,
            duration_seconds=duration,
            exit_code=exit_code,
            diagnostics=diagnostics,
            cancelled=self._cancel_requested,
        )
