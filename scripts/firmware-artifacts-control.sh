#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

export HEXEVOICE_FIRMWARE_ARTIFACT_DIR="${HEXEVOICE_FIRMWARE_ARTIFACT_DIR:-${FIRMWARE_ARTIFACT_DIR:-$ROOT_DIR/runtime/firmware}}"
export HEXEVOICE_FIRMWARE_SOURCE="${HEXEVOICE_FIRMWARE_SOURCE:-auto}"
export HEXEVOICE_FIRMWARE_REF="${HEXEVOICE_FIRMWARE_REF:-main}"
export HEXEVOICE_FIRMWARE_REPO_ARTIFACT_DIR="${HEXEVOICE_FIRMWARE_REPO_ARTIFACT_DIR:-runtime/firmware}"
export HEXEVOICE_FIRMWARE_ARTIFACTS="${HEXEVOICE_FIRMWARE_ARTIFACTS:-hexe_firmware.bin,hexe_firmware_esp_box_3.bin,hexe_firmware_ha_voice_pe.bin,manifest.json,manifest-esp_box_3.json,manifest-ha_voice_pe.json,SHA256SUMS}"
export HEXEVOICE_FIRMWARE_REQUIRED_PROFILES="${HEXEVOICE_FIRMWARE_REQUIRED_PROFILES:-esp_box_3,ha_voice_pe}"

run_python() {
  "$PYTHON_BIN" - "$@" <<'PY'
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request


@dataclass(frozen=True)
class Artifact:
    name: str
    url: str | None = None
    source_path: Path | None = None


def split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def artifact_dir() -> Path:
    return Path(os.environ["HEXEVOICE_FIRMWARE_ARTIFACT_DIR"]).expanduser().resolve()


def artifact_names() -> list[str]:
    return split_csv(os.environ.get("HEXEVOICE_FIRMWARE_ARTIFACTS"))


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        shutil.copy2(source, tmp_path)
        if tmp_path.stat().st_size <= 0:
            raise SystemExit(f"empty_artifact:{source.name}")
        tmp_path.replace(destination)
    finally:
        tmp_path.unlink(missing_ok=True)


def atomic_download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with urllib.request.urlopen(url, timeout=120) as response, tmp_path.open("wb") as output:
            shutil.copyfileobj(response, output)
        if tmp_path.stat().st_size <= 0:
            raise SystemExit(f"empty_download:{destination.name}")
        tmp_path.replace(destination)
    finally:
        tmp_path.unlink(missing_ok=True)


def filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    if not name:
        raise SystemExit(f"firmware_artifact_url_missing_filename:{url}")
    return name


def source_from_local_dir(source_dir: Path) -> list[Artifact]:
    if not source_dir.is_dir():
        raise SystemExit(f"firmware_source_dir_missing:{source_dir}")
    names = artifact_names()
    artifacts: list[Artifact] = []
    for name in names:
        path = source_dir / name
        if path.is_file():
            artifacts.append(Artifact(name=name, source_path=path))
    if not artifacts:
        raise SystemExit(f"firmware_source_dir_has_no_supported_artifacts:{source_dir}")
    return artifacts


def source_from_urls(urls: list[str]) -> list[Artifact]:
    return [Artifact(name=filename_from_url(url), url=url) for url in urls]


def source_from_base_url(base_url: str) -> list[Artifact]:
    base = base_url.rstrip("/")
    return [Artifact(name=name, url=f"{base}/{name}") for name in artifact_names()]


def source_from_git_repo(repo_url: str) -> list[Artifact]:
    ref = os.environ["HEXEVOICE_FIRMWARE_REF"]
    rel = Path(os.environ["HEXEVOICE_FIRMWARE_REPO_ARTIFACT_DIR"])
    with tempfile.TemporaryDirectory(prefix="hexevoice-firmware-repo-") as tmp:
        checkout = Path(tmp) / "repo"
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", ref, repo_url, str(checkout)],
            text=True,
            check=True,
        )
        artifacts = source_from_local_dir(checkout / rel)
        materialized: list[Artifact] = []
        staging = Path(tmp) / "artifacts"
        staging.mkdir()
        for artifact in artifacts:
            assert artifact.source_path is not None
            copied = staging / artifact.name
            shutil.copy2(artifact.source_path, copied)
            materialized.append(Artifact(name=artifact.name, source_path=copied))
        target = artifact_dir()
        install_artifacts(materialized, target)
        return []


def github_repo_slug(repo_url: str | None, repository: str | None) -> str:
    if repository:
        return repository.strip().removeprefix("https://github.com/").removesuffix(".git")
    if not repo_url:
        raise SystemExit("firmware_github_repository_missing")
    parsed = urlparse(repo_url)
    if parsed.netloc != "github.com":
        raise SystemExit("firmware_github_repository_missing")
    slug = parsed.path.strip("/").removesuffix(".git")
    if slug.count("/") != 1:
        raise SystemExit(f"firmware_github_repository_invalid:{repo_url}")
    return slug


def source_from_github_release() -> list[Artifact]:
    slug = github_repo_slug(
        os.environ.get("HEXEVOICE_FIRMWARE_REPO_URL"),
        os.environ.get("HEXEVOICE_FIRMWARE_GITHUB_REPOSITORY"),
    )
    tag = os.environ.get("HEXEVOICE_FIRMWARE_RELEASE_TAG", "latest")
    api_url = (
        f"https://api.github.com/repos/{slug}/releases/latest"
        if tag in {"", "latest"}
        else f"https://api.github.com/repos/{slug}/releases/tags/{tag}"
    )
    request = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.loads(response.read().decode("utf-8"))
    wanted = set(artifact_names())
    artifacts = [
        Artifact(name=str(asset["name"]), url=str(asset["browser_download_url"]))
        for asset in release.get("assets", [])
        if asset.get("name") in wanted and asset.get("browser_download_url")
    ]
    if not artifacts:
        raise SystemExit(f"firmware_github_release_has_no_supported_assets:{slug}:{tag}")
    return artifacts


def selected_source() -> list[Artifact]:
    mode = os.environ.get("HEXEVOICE_FIRMWARE_SOURCE", "auto")
    source_dir = os.environ.get("HEXEVOICE_FIRMWARE_SOURCE_DIR")
    urls = split_csv(os.environ.get("HEXEVOICE_FIRMWARE_ARTIFACT_URLS"))
    base_url = os.environ.get("HEXEVOICE_FIRMWARE_ARTIFACT_BASE_URL") or os.environ.get("HEXEVOICE_FIRMWARE_RELEASE_ASSET_BASE_URL")
    repo_url = os.environ.get("HEXEVOICE_FIRMWARE_REPO_URL")
    release_url = os.environ.get("HEXEVOICE_FIRMWARE_RELEASE_URL")

    if release_url and not base_url:
        base_url = release_url
    if mode == "local" or (mode == "auto" and source_dir):
        if not source_dir:
            raise SystemExit("firmware_source_dir_not_configured")
        return source_from_local_dir(Path(source_dir).expanduser())
    if mode == "urls" or (mode == "auto" and urls):
        if not urls:
            raise SystemExit("firmware_artifact_urls_not_configured")
        return source_from_urls(urls)
    if mode == "base-url" or (mode == "auto" and base_url):
        if not base_url:
            raise SystemExit("firmware_artifact_base_url_not_configured")
        return source_from_base_url(base_url)
    if mode == "github-release":
        return source_from_github_release()
    if mode == "git" or (mode == "auto" and repo_url):
        if not repo_url:
            raise SystemExit("firmware_repo_url_not_configured")
        return source_from_git_repo(repo_url)
    raise SystemExit(
        "firmware_source_not_configured: set HEXEVOICE_FIRMWARE_SOURCE_DIR, "
        "HEXEVOICE_FIRMWARE_ARTIFACT_URLS, HEXEVOICE_FIRMWARE_ARTIFACT_BASE_URL, "
        "or HEXEVOICE_FIRMWARE_REPO_URL"
    )


def install_artifacts(artifacts: list[Artifact], target: Path) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for artifact in artifacts:
        destination = target / artifact.name
        if artifact.source_path is not None:
            atomic_copy(artifact.source_path, destination)
        elif artifact.url is not None:
            atomic_download(artifact.url, destination)
        else:
            raise SystemExit(f"firmware_artifact_has_no_source:{artifact.name}")
        written.append(artifact.name)
        print(f"installed {artifact.name}")
    return written


def verify_checksums(target: Path) -> None:
    checksum_path = target / "SHA256SUMS"
    if not checksum_path.exists():
        print("checksums: unavailable")
        return
    failures: list[str] = []
    checked = 0
    for raw_line in checksum_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            failures.append(f"invalid_checksum_line:{raw_line}")
            continue
        expected, filename = parts[0], parts[-1].lstrip("*")
        path = target / filename
        if not path.exists():
            failures.append(f"missing_checksum_artifact:{filename}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        checked += 1
        if actual.lower() != expected.lower():
            failures.append(f"checksum_mismatch:{filename}")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"checksums: ok ({checked})")


def verify_required_profiles(target: Path) -> None:
    missing: list[str] = []
    for profile in split_csv(os.environ.get("HEXEVOICE_FIRMWARE_REQUIRED_PROFILES")):
        artifact = target / f"hexe_firmware_{profile}.bin"
        if profile == "esp_box_3" and not artifact.exists():
            artifact = target / "hexe_firmware.bin"
        manifest = target / f"manifest-{profile}.json"
        if not artifact.exists():
            missing.append(f"unsupported_board_artifact:{profile}:{artifact.name}")
        if not manifest.exists():
            missing.append(f"unsupported_board_manifest:{profile}:{manifest.name}")
    if missing:
        raise SystemExit("\n".join(missing))


def list_artifacts(target: Path) -> None:
    if not target.exists():
        print(f"artifact dir missing: {target}")
        return
    for path in sorted(target.iterdir()):
        if path.is_file():
            print(path.name)


def source_configured() -> str:
    source_dir = os.environ.get("HEXEVOICE_FIRMWARE_SOURCE_DIR")
    urls = split_csv(os.environ.get("HEXEVOICE_FIRMWARE_ARTIFACT_URLS"))
    base_url = os.environ.get("HEXEVOICE_FIRMWARE_ARTIFACT_BASE_URL") or os.environ.get("HEXEVOICE_FIRMWARE_RELEASE_ASSET_BASE_URL")
    release_url = os.environ.get("HEXEVOICE_FIRMWARE_RELEASE_URL")
    repo_url = os.environ.get("HEXEVOICE_FIRMWARE_REPO_URL")
    github_repo = os.environ.get("HEXEVOICE_FIRMWARE_GITHUB_REPOSITORY")
    if source_dir:
        return f"local ({source_dir})"
    if urls:
        return f"urls ({len(urls)})"
    if base_url or release_url:
        return f"base-url ({base_url or release_url})"
    if github_repo:
        return f"github-release ({github_repo})"
    if repo_url:
        return f"repo ({repo_url})"
    return "missing"


def doctor() -> None:
    target = artifact_dir()
    print(f"artifact dir: {target}")
    if target.is_dir():
        print("artifact dir: ok")
    else:
        print("artifact dir: missing")
    print(f"source: {source_configured()}")
    try:
        verify_required_profiles(target)
        print("required profiles: ok")
    except SystemExit as exc:
        print(str(exc))
    verify_checksums(target)


action = sys.argv[1] if len(sys.argv) > 1 else "list"
target = artifact_dir()
if action in {"download", "sync", "install"}:
    artifacts = selected_source()
    if artifacts:
        install_artifacts(artifacts, target)
    verify_required_profiles(target)
    verify_checksums(target)
elif action == "verify":
    verify_required_profiles(target)
    verify_checksums(target)
elif action == "list":
    list_artifacts(target)
elif action == "doctor":
    doctor()
else:
    raise SystemExit("Usage: firmware-artifacts-control.sh {download|sync|install|verify|list|doctor}")
PY
}

ACTION="${1:-list}"
case "$ACTION" in
  download|sync|install|verify|list|doctor)
    run_python "$ACTION"
    ;;
  *)
    echo "Usage: $0 {download|sync|install|verify|list|doctor}"
    exit 1
    ;;
esac
