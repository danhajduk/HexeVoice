from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from .models import BuildProfile, Target


CONFIG_PATH = Path.home() / ".config" / "hexevoice" / "firmware-tui.json"
PRIVATE_ENV_MARKERS = ("password", "secret", "token", "private_key", "credential")


def _load_board_yaml(path: Path) -> dict[str, Any]:
    tools_dir = path.parents[2] / "tools"
    import sys

    sys.path.insert(0, str(tools_dir))
    from validate_board_profiles import load_profile, validate_profile  # type: ignore[import-not-found]

    payload = load_profile(path)
    validate_profile(payload, path)
    return payload


def discover_targets(project_dir: Path) -> list[Target]:
    targets: list[Target] = []
    for path in sorted((project_dir / "firmware" / "boards").glob("*/board.yaml")):
        payload = _load_board_yaml(path)
        if not payload["adapters"]["buildable"]:
            continue
        build = payload["build"]
        hardware = payload["hardware"]
        targets.append(
            Target(
                name=payload["board_profile"],
                display_name=payload["display_name"],
                idf_target=build["idf_target"],
                partition_schema=build["partition_schema"],
                flash_size=hardware["flash_size"],
                app_slot_size=build["app_slot_size"],
                required_idf_version=build.get("required_idf_version"),
            )
        )
    return targets


class ProfileStore:
    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path

    def load(self, project_dir: Path, targets: list[Target]) -> list[BuildProfile]:
        if not self.path.exists():
            compiler = project_dir / "scripts" / "rebuild-firmware.sh"
            return [BuildProfile(name=target.display_name, project_dir=project_dir, compiler=compiler, target=target.name) for target in targets]
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return [self._decode(item) for item in payload.get("profiles", [])]

    def save(self, profiles: list[BuildProfile]) -> None:
        for profile in profiles:
            private_keys = [key for key in profile.environment if any(marker in key.lower() for marker in PRIVATE_ENV_MARKERS)]
            if private_keys:
                raise ValueError(f"Refusing to save secret-like environment keys: {', '.join(private_keys)}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "profiles": [self._encode(profile) for profile in profiles]}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _encode(profile: BuildProfile) -> dict[str, Any]:
        payload = asdict(profile)
        payload["project_dir"] = str(profile.project_dir)
        payload["compiler"] = str(profile.compiler)
        return payload

    @staticmethod
    def _decode(payload: dict[str, Any]) -> BuildProfile:
        return BuildProfile(
            name=payload["name"],
            project_dir=Path(payload["project_dir"]),
            compiler=Path(payload["compiler"]),
            target=payload["target"],
            configuration=payload.get("configuration", "Release"),
            arguments=list(payload.get("arguments", [])),
            environment=dict(payload.get("environment", {})),
            run_command=list(payload.get("run_command", [])),
        )
