from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/ui-screen-menu.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ui_screen_menu", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_screen_ids_preserves_index_order(tmp_path):
    module = load_module()
    screens = tmp_path / "screens"
    screens.mkdir()
    (screens / "idle.yaml").write_text("id: idle\n", encoding="utf-8")
    (screens / "timer.yaml").write_text("id: timer\n", encoding="utf-8")
    index = tmp_path / "screens_layout.yaml"
    index.write_text(
        "screens:\n  - !include screens/timer.yaml\n  - !include screens/idle.yaml\n",
        encoding="utf-8",
    )

    assert module.load_screen_ids(index) == ["timer", "idle"]


def test_load_screen_ids_rejects_duplicate_ids(tmp_path):
    module = load_module()
    screens = tmp_path / "screens"
    screens.mkdir()
    (screens / "one.yaml").write_text("id: idle\n", encoding="utf-8")
    (screens / "two.yaml").write_text("id: idle\n", encoding="utf-8")
    index = tmp_path / "screens_layout.yaml"
    index.write_text(
        "screens:\n  - !include screens/one.yaml\n  - !include screens/two.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="duplicate screen id: idle"):
        module.load_screen_ids(index)


def test_send_screen_posts_expected_api_payload(monkeypatch):
    module = load_module()
    captured = {}

    def fake_request(base_url, path, **kwargs):
        captured.update({"base_url": base_url, "path": path, **kwargs})
        return {"accepted": True}

    monkeypatch.setattr(module, "request_json", fake_request)

    response = module.send_screen("http://node:9004", "p4-7b", "idle", 10, 3.0)

    assert response == {"accepted": True}
    assert captured == {
        "base_url": "http://node:9004",
        "path": "/api/endpoint/ui/screen",
        "method": "POST",
        "payload": {
            "endpoint_id": "p4-7b",
            "screen_id": "idle",
            "duration_seconds": 10,
        },
        "timeout": 3.0,
    }
