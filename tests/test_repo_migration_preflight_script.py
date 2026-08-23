from __future__ import annotations

import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT_DIR / "scripts" / "repo-migration-preflight.sh"


def test_repo_migration_preflight_help_documents_checks():
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--help"],
        cwd=ROOT_DIR,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "full backend pytest suite" in result.stdout
    assert "frontend production build" in result.stdout
    assert "npm audit --omit=dev" in result.stdout
    assert "non-blocking full dev audit review" in result.stdout


def test_repo_migration_preflight_runs_required_and_review_sections():
    content = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "run_required \"backend pytest suite\"" in content
    assert "run_required \"frontend production build\"" in content
    assert "run_required \"production npm audit\"" in content
    assert "npm audit --omit=dev" in content
    assert "run_dev_audit" in content
