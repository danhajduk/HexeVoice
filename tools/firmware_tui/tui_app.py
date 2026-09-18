from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, ProgressBar, RichLog, Select, Static, TextArea

from .compiler import AsyncBuildRunner, FirmwareCompiler, escaped_command
from .models import BuildAction, BuildProfile, BuildResult, Diagnostic, Severity, Target
from .profiles import ProfileStore, discover_targets


class StatusCard(Static):
    def __init__(self, title: str, value: str, *, id: str) -> None:
        super().__init__(id=id, classes="status-card")
        self.title = title
        self.value = value

    def compose(self) -> ComposeResult:
        yield Label(self.title, classes="card-title")
        yield Label(self.value, classes="card-value")

    def set_value(self, value: str, state: str = "") -> None:
        label = self.query_one(".card-value", Label)
        label.update(value)
        label.set_classes(f"card-value {state}".strip())


class ProfileScreen(ModalScreen[BuildProfile | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, profile: BuildProfile, targets: list[Target]) -> None:
        super().__init__()
        self.profile = profile
        self.targets = targets

    def compose(self) -> ComposeResult:
        target_options = [(target.display_name, target.name) for target in self.targets]
        env_text = "\n".join(f"{key}={value}" for key, value in sorted(self.profile.environment.items()))
        with Container(id="profile-dialog"):
            yield Label("BUILD PROFILE", id="dialog-title")
            yield Label("Project directory", classes="field-label")
            yield Input(str(self.profile.project_dir), id="project-input")
            yield Label("Compiler executable", classes="field-label")
            yield Input(str(self.profile.compiler), id="compiler-input")
            with Horizontal(classes="dialog-row"):
                with Vertical():
                    yield Label("Target", classes="field-label")
                    yield Select(target_options, value=self.profile.target, id="target-select")
                with Vertical():
                    yield Label("Configuration", classes="field-label")
                    yield Select([("Debug", "Debug"), ("Release", "Release")], value=self.profile.configuration, id="config-select")
            yield Label("Additional compiler arguments", classes="field-label")
            yield Input(shlex.join(self.profile.arguments), id="profile-args-input")
            yield Label("Environment variables (KEY=VALUE)", classes="field-label")
            yield TextArea(env_text, id="environment-editor", language=None)
            yield Label("Run command", classes="field-label")
            yield Input(shlex.join(self.profile.run_command), id="run-command-input")
            yield Label("", id="profile-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel-profile")
                yield Button("Save profile", id="save-profile", variant="primary")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-profile":
            self.dismiss(None)
            return
        try:
            environment: dict[str, str] = {}
            for line in self.query_one("#environment-editor", TextArea).text.splitlines():
                if not line.strip():
                    continue
                key, separator, value = line.partition("=")
                if not separator or not key.strip():
                    raise ValueError(f"Invalid environment entry: {line}")
                environment[key.strip()] = value
            target = self.query_one("#target-select", Select).value
            configuration = self.query_one("#config-select", Select).value
            updated = BuildProfile(
                name=self.profile.name,
                project_dir=Path(self.query_one("#project-input", Input).value).expanduser().resolve(),
                compiler=Path(self.query_one("#compiler-input", Input).value).expanduser().resolve(),
                target=str(target),
                configuration=str(configuration),
                arguments=shlex.split(self.query_one("#profile-args-input", Input).value),
                environment=environment,
                run_command=shlex.split(self.query_one("#run-command-input", Input).value),
            )
            self.dismiss(updated)
        except ValueError as exc:
            self.query_one("#profile-error", Label).update(str(exc))


class FirmwareBuildApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "Hexe Firmware Console"
    SUB_TITLE = "Compiler and diagnostics"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("b", "build", "Build"),
        ("r", "rebuild", "Rebuild"),
        ("c", "clean", "Clean"),
        ("x", "cancel_build", "Cancel"),
        ("f5", "build_run", "Build + Run"),
        ("e", "focus_diagnostics", "Diagnostics"),
        ("o", "open_diagnostic", "Open diagnostic"),
        ("p", "edit_profile", "Project/profile"),
        ("t", "focus_targets", "Targets"),
        ("a", "focus_arguments", "Arguments"),
        ("enter", "inspect_selected", "Inspect"),
        ("escape", "focus_targets", "Main table"),
    ]

    _progress_pattern = re.compile(r"\[(?P<current>\d+)/(?P<total>\d+)\]")

    def __init__(self, project_dir: Path) -> None:
        super().__init__()
        self.project_dir = project_dir.resolve()
        self.targets = discover_targets(self.project_dir)
        self.profile_store = ProfileStore()
        self.profiles = self.profile_store.load(self.project_dir, self.targets)
        if not self.profiles:
            raise RuntimeError("No buildable firmware profiles were found")
        self.profile = self.profiles[0]
        self.compiler = FirmwareCompiler()
        self.runner = AsyncBuildRunner()
        self.last_result: BuildResult | None = None
        self.diagnostics: list[Diagnostic] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="app-body"):
            with Horizontal(id="status-cards"):
                yield StatusCard("COMPILER / TOOLCHAIN", str(self.profile.compiler), id="compiler-card")
                yield StatusCard("SELECTED TARGET", self.profile.target, id="target-card")
                yield StatusCard("CONFIGURATION", self.profile.configuration, id="config-card")
                yield StatusCard("LAST BUILD", "Not built", id="build-card")
            with Container(id="command-panel"):
                with Horizontal(id="phase-row"):
                    yield Label("IDLE", id="phase-label")
                    yield ProgressBar(total=100, show_eta=False, id="build-progress")
                yield Label("Exact command will appear here", id="command-preview")
                yield Input(placeholder="Additional compiler arguments", id="arguments-input")
                with Horizontal(id="action-buttons"):
                    yield Button("Build", id="build", variant="primary")
                    yield Button("Rebuild", id="rebuild")
                    yield Button("Clean", id="clean")
                    yield Button("Build + Run", id="build-run", variant="success")
                    yield Button("Cancel", id="cancel", variant="error", disabled=True)
            with Horizontal(id="workspace"):
                with Vertical(id="navigator-pane"):
                    yield Label("TARGETS AND PROFILES", classes="section-title")
                    yield DataTable(id="navigator", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="details-pane"):
                    yield Label("DETAILS", classes="section-title")
                    yield TextArea("", id="details", read_only=True)
            with Vertical(id="output-section"):
                yield Label("BUILD OUTPUT", classes="section-title")
                yield RichLog(id="build-output", highlight=True, markup=False, wrap=False, auto_scroll=True)
            with Vertical(id="diagnostics-section"):
                yield Label("DIAGNOSTICS", classes="section-title")
                yield DataTable(id="diagnostics", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        navigator = self.query_one("#navigator", DataTable)
        navigator.add_columns("Target", "Configuration", "Toolchain")
        for index, profile in enumerate(self.profiles):
            target = self._target(profile.target)
            toolchain = f"ESP-IDF {target.required_idf_version}" if target.required_idf_version else "Active ESP-IDF"
            navigator.add_row(profile.target, profile.configuration, toolchain, key=str(index))
        diagnostics = self.query_one("#diagnostics", DataTable)
        diagnostics.add_columns("Severity", "File", "Line", "Column", "Message")
        navigator.focus()
        self._refresh_profile_view()

    def on_resize(self, event: Resize) -> None:
        self.screen.set_class(event.size.width < 100, "narrow")

    def _target(self, name: str) -> Target:
        return next(target for target in self.targets if target.name == name)

    def _refresh_profile_view(self) -> None:
        target = self._target(self.profile.target)
        self.query_one("#compiler-card", StatusCard).set_value(target.required_idf_version and f"ESP-IDF {target.required_idf_version}" or "Active ESP-IDF")
        self.query_one("#target-card", StatusCard).set_value(target.display_name)
        self.query_one("#config-card", StatusCard).set_value(self.profile.configuration)
        details = (
            f"Profile: {self.profile.name}\n"
            f"Board: {target.display_name}\n"
            f"Target: {target.idf_target}\n"
            f"Partition: {target.partition_schema}\n"
            f"Flash: {target.flash_size}\n"
            f"App slot: {target.app_slot_size}\n"
            f"Compiler: {self.profile.compiler}\n"
            f"Project: {self.profile.project_dir}"
        )
        self.query_one("#details", TextArea).text = details
        self.query_one("#arguments-input", Input).value = shlex.join(self.profile.arguments)
        self._preview(BuildAction.BUILD)

    def _preview(self, action: BuildAction) -> None:
        try:
            profile = self._profile_with_live_arguments()
            command = self.compiler.command_for(profile, action)
            self.query_one("#command-preview", Label).update(f"$ {escaped_command(command.argv)}")
        except ValueError as exc:
            self.query_one("#command-preview", Label).update(str(exc))

    def _profile_with_live_arguments(self) -> BuildProfile:
        return BuildProfile(
            name=self.profile.name,
            project_dir=self.profile.project_dir,
            compiler=self.profile.compiler,
            target=self.profile.target,
            configuration=self.profile.configuration,
            arguments=shlex.split(self.query_one("#arguments-input", Input).value),
            environment=dict(self.profile.environment),
            run_command=list(self.profile.run_command),
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "navigator" or event.row_key is None:
            return
        self.profile = self.profiles[int(str(event.row_key.value))]
        self._refresh_profile_view()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "arguments-input":
            self._preview(BuildAction.BUILD)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "build": self.action_build,
            "rebuild": self.action_rebuild,
            "clean": self.action_clean,
            "build-run": self.action_build_run,
            "cancel": self.action_cancel_build,
        }
        if handler := actions.get(event.button.id or ""):
            handler()

    def action_build(self) -> None:
        self.execute_action(BuildAction.BUILD)

    def action_rebuild(self) -> None:
        self.execute_action(BuildAction.REBUILD)

    def action_clean(self) -> None:
        self.execute_action(BuildAction.CLEAN)

    def action_build_run(self) -> None:
        self.build_and_run()

    def action_cancel_build(self) -> None:
        if self.runner.running:
            asyncio.create_task(self.runner.cancel())

    def action_focus_diagnostics(self) -> None:
        self.query_one("#diagnostics", DataTable).focus()

    def action_focus_targets(self) -> None:
        self.query_one("#navigator", DataTable).focus()

    def action_focus_arguments(self) -> None:
        self.query_one("#arguments-input", Input).focus()

    def action_inspect_selected(self) -> None:
        self.query_one("#details", TextArea).focus()

    def action_open_diagnostic(self) -> None:
        table = self.query_one("#diagnostics", DataTable)
        if table.row_count == 0 or table.cursor_row >= len(self.diagnostics):
            return
        diagnostic = self.diagnostics[table.cursor_row]
        if diagnostic.file:
            self._open_location(diagnostic)

    def action_edit_profile(self) -> None:
        self.push_screen(ProfileScreen(self.profile, self.targets), self._profile_updated)

    def _profile_updated(self, profile: BuildProfile | None) -> None:
        if profile is None:
            return
        index = self.profiles.index(self.profile)
        updated_profiles = [*self.profiles]
        updated_profiles[index] = profile
        try:
            self.profile_store.save(updated_profiles)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        self.profiles = updated_profiles
        self.profile = profile
        self._refresh_profile_view()
        self.notify("Build profile saved", severity="information")

    @work(exclusive=True, group="build")
    async def execute_action(self, action: BuildAction) -> BuildResult | None:
        return await self._execute_action(action)

    async def _execute_action(self, action: BuildAction) -> BuildResult | None:
        if self.runner.running:
            self.notify("A compiler process is already running", severity="warning")
            return None
        try:
            profile = self._profile_with_live_arguments()
            command = self.compiler.command_for(profile, action)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return None
        self._set_running(True, action)
        self.diagnostics = []
        self.query_one("#diagnostics", DataTable).clear()
        output = self.query_one("#build-output", RichLog)
        output.clear()
        output.write(Text(f"$ {escaped_command(command.argv)}", style="bold cyan"))

        async def on_output(line: str, is_stderr: bool) -> None:
            output.write(Text.from_ansi(line), scroll_end=True)
            if match := self._progress_pattern.search(line):
                current, total = int(match.group("current")), int(match.group("total"))
                self.query_one("#build-progress", ProgressBar).update(total=total, progress=current)

        try:
            result = await self.runner.run(command, action, on_output)
        except (OSError, RuntimeError) as exc:
            output.write(Text(str(exc), style="bold red"))
            self.query_one("#phase-label", Label).update("FAILED TO START")
            self._set_running(False, action)
            return None
        self.last_result = result
        self.diagnostics = result.diagnostics
        self._show_result(result)
        self._set_running(False, action)
        return result

    @work(exclusive=True, group="build")
    async def build_and_run(self) -> None:
        build_result = await self._execute_action(BuildAction.BUILD)
        if build_result and build_result.exit_code == 0 and not build_result.cancelled:
            await self._execute_action(BuildAction.RUN)

    def _set_running(self, running: bool, action: BuildAction) -> None:
        self.query_one("#cancel", Button).disabled = not running
        for button_id in ("#build", "#rebuild", "#clean", "#build-run"):
            self.query_one(button_id, Button).disabled = running
        self.query_one("#phase-label", Label).update(action.value.upper() if running else "IDLE")
        if running:
            self.query_one("#build-progress", ProgressBar).update(total=100, progress=0)

    def _show_result(self, result: BuildResult) -> None:
        table = self.query_one("#diagnostics", DataTable)
        for diagnostic in result.diagnostics:
            severity = Text(diagnostic.severity.upper(), style="bold red" if diagnostic.severity == Severity.ERROR else "bold yellow")
            table.add_row(
                severity,
                str(diagnostic.file or ""),
                str(diagnostic.line or ""),
                str(diagnostic.column or ""),
                diagnostic.message,
            )
        if result.cancelled:
            state, style = "Cancelled", "warning"
        elif result.exit_code == 0:
            state, style = "Success", "success"
        else:
            state, style = f"Failed ({result.exit_code})", "error"
        summary = f"{state}  {result.duration_seconds:.1f}s  W:{result.warning_count} E:{result.error_count}"
        self.query_one("#build-card", StatusCard).set_value(summary, style)
        self.query_one("#phase-label", Label).update(state.upper())
        self.query_one("#build-progress", ProgressBar).update(total=100, progress=100 if result.exit_code == 0 else 0)

    @staticmethod
    def _open_location(diagnostic: Diagnostic) -> None:
        if diagnostic.file is None:
            return
        editor = os.environ.get("EDITOR")
        if editor:
            argv = shlex.split(editor)
            if diagnostic.line:
                argv.append(f"+{diagnostic.line}")
            argv.append(str(diagnostic.file))
        elif shutil.which("code"):
            location = f"{diagnostic.file}:{diagnostic.line or 1}:{diagnostic.column or 1}"
            argv = ["code", "--goto", location]
        else:
            argv = ["xdg-open", str(diagnostic.file)]
        subprocess.Popen(argv, start_new_session=True)
