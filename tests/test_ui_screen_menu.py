from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

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


def test_choose_accepts_empty_input_as_default(monkeypatch):
    module = load_module()
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    assert module.choose(
        "Duration",
        [("5", "5 seconds"), ("10", "10 seconds")],
        default="5",
    ) == "5"


def test_choose_renders_three_columns(monkeypatch, capsys):
    module = load_module()
    monkeypatch.setattr("builtins.input", lambda _prompt: "4")

    selected = module.choose(
        "Screen",
        [(name, name) for name in ("idle", "timer", "listening", "thinking")],
        columns=3,
    )

    output = capsys.readouterr().out.splitlines()
    assert selected == "thinking"
    assert any("1. idle" in line and "2. timer" in line and "3. listening" in line for line in output)
    assert any("4. thinking" in line for line in output)


def test_interactive_menu_returns_to_screen_selection_after_send(monkeypatch):
    module = load_module()
    args = SimpleNamespace(
        api_base_url="http://node:9004",
        endpoint_id="p4-7b",
        screen=None,
        duration=None,
        config=Path("unused.yaml"),
        timeout=3.0,
        list_screens=False,
    )
    labels = []
    sent = []

    class Parser:
        def parse_args(self):
            return args

    def fake_choose(label, _options, **_kwargs):
        labels.append(label)
        if labels == ["Screen"]:
            return "idle"
        if labels == ["Screen", "Duration"]:
            return "5"
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "build_parser", lambda: Parser())
    monkeypatch.setattr(module, "load_screen_ids", lambda _path: ["idle", "timer"])
    monkeypatch.setattr(module, "choose", fake_choose)
    monkeypatch.setattr(
        module,
        "send_screen",
        lambda base_url, endpoint_id, screen_id, duration, timeout: sent.append(
            (base_url, endpoint_id, screen_id, duration, timeout)
        )
        or {"accepted": True},
    )

    assert module.main() == 130
    assert labels == ["Screen", "Duration", "Screen"]
    assert sent == [("http://node:9004", "p4-7b", "idle", 5, 3.0)]
