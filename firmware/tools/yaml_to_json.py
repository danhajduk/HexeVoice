#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


class IncludeLoader(yaml.SafeLoader):
    pass


def _include(loader: IncludeLoader, node: yaml.Node) -> Any:
    relative_path = loader.construct_scalar(node)
    include_path = (loader._current_file.parent / relative_path).resolve()
    try:
        include_path.relative_to(loader._source_root)
    except ValueError as exc:
        raise yaml.YAMLError(f"include escapes source directory: {relative_path}") from exc
    return _load_yaml(include_path, loader._source_root, loader._include_stack)


IncludeLoader.add_constructor("!include", _include)


def _load_yaml(path: Path, source_root: Path, include_stack: tuple[Path, ...]) -> Any:
    resolved = path.resolve()
    if resolved in include_stack:
        chain = " -> ".join(str(item) for item in (*include_stack, resolved))
        raise yaml.YAMLError(f"cyclic YAML include: {chain}")
    if not resolved.is_file():
        raise yaml.YAMLError(f"included YAML file not found: {resolved}")
    loader = IncludeLoader(resolved.read_text(encoding="utf-8"))
    loader._current_file = resolved
    loader._source_root = source_root
    loader._include_stack = (*include_stack, resolved)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def load_yaml_with_includes(path: Path) -> Any:
    resolved = path.resolve()
    return _load_yaml(resolved, resolved.parent, ())


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert YAML with relative !include tags to JSON.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    payload = load_yaml_with_includes(args.input)
    if not isinstance(payload, dict):
        raise SystemExit(f"YAML metadata root must be an object: {args.input}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
