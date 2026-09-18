from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from tools.firmware_tui.compiler import AsyncBuildRunner, FirmwareCompiler
from tools.firmware_tui.diagnostics import CompositeDiagnosticParser
from tools.firmware_tui.models import BuildAction, BuildProfile, CommandSpec, Severity


def profile(tmp_path: Path) -> BuildProfile:
    return BuildProfile(
        name="test",
        project_dir=tmp_path,
        compiler=tmp_path / "rebuild-firmware.sh",
        target="waveshare_p4_wifi6_touch_lcd_7b",
        configuration="Debug",
        arguments=["--project-version", "version with spaces"],
        environment={"SAFE_FLAG": "1"},
    )


def test_firmware_command_is_an_argument_list_without_a_shell(tmp_path: Path):
    command = FirmwareCompiler().command_for(profile(tmp_path), BuildAction.REBUILD)

    assert command.argv == [
        str(tmp_path / "rebuild-firmware.sh"),
        "--profile",
        "waveshare_p4_wifi6_touch_lcd_7b",
        "--clean",
        "--verbose",
        "--project-version",
        "version with spaces",
    ]
    assert command.environment == {
        "SAFE_FLAG": "1",
        "CMAKE_BUILD_TYPE": "Debug",
        "HEXE_BUILD_CONFIGURATION": "Debug",
    }

    clean = FirmwareCompiler().command_for(profile(tmp_path), BuildAction.CLEAN)
    assert clean.argv == [
        str(tmp_path / "rebuild-firmware.sh"),
        "--profile",
        "waveshare_p4_wifi6_touch_lcd_7b",
        "--clean-only",
        "--project-version",
        "version with spaces",
    ]


def test_compiler_diagnostics_are_structured():
    parser = CompositeDiagnosticParser()
    diagnostic = parser.parse_line("src/main.cpp:42:7: warning: unused variable 'value'")

    assert diagnostic is not None
    assert diagnostic.file == Path("src/main.cpp")
    assert diagnostic.line == 42
    assert diagnostic.column == 7
    assert diagnostic.severity == Severity.WARNING
    assert diagnostic.message == "unused variable 'value'"


def test_failed_build_streams_output_and_returns_exit_code(tmp_path: Path):
    async def scenario():
        lines: list[tuple[str, bool]] = []
        command = CommandSpec(
            argv=[sys.executable, "-c", "import sys; print('main.c:3:2: error: broken'); sys.exit(7)"],
            cwd=tmp_path,
        )
        result = await AsyncBuildRunner().run(command, BuildAction.BUILD, lambda line, stderr: lines.append((line, stderr)))
        return result, lines

    result, lines = asyncio.run(scenario())
    assert result.exit_code == 7
    assert result.error_count == 1
    assert lines == [("main.c:3:2: error: broken", False)]


def test_active_build_can_be_cancelled(tmp_path: Path):
    async def scenario():
        runner = AsyncBuildRunner()
        command = CommandSpec(
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
        )
        task = asyncio.create_task(runner.run(command, BuildAction.BUILD))
        for _ in range(100):
            if runner.running:
                break
            await asyncio.sleep(0.01)
        await runner.cancel()
        return await asyncio.wait_for(task, timeout=5)

    result = asyncio.run(scenario())
    assert result.cancelled is True
    assert result.exit_code != 0
