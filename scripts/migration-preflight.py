#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from hexevoice.api.models import NodeMigrationPreflightRequest  # noqa: E402
from hexevoice.config.settings import Settings  # noqa: E402
from hexevoice.migration import NodeMigrationService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run HexeVoice migration preflight checks.")
    parser.add_argument("bundle", type=Path, help="Migration bundle JSON file.")
    parser.add_argument("--core-url", dest="destination_core_base_url")
    parser.add_argument("--api-url", dest="destination_api_base_url")
    parser.add_argument("--ui-url", dest="destination_ui_endpoint")
    parser.add_argument("--hostname", dest="destination_hostname")
    parser.add_argument("--check-core", action="store_true", help="Attempt a live Core URL reachability check.")
    args = parser.parse_args()

    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    payload = NodeMigrationPreflightRequest(
        bundle=bundle,
        destination_core_base_url=args.destination_core_base_url,
        destination_api_base_url=args.destination_api_base_url,
        destination_ui_endpoint=args.destination_ui_endpoint,
        destination_hostname=args.destination_hostname,
        check_core_reachability=args.check_core,
        dry_run=True,
    )
    result = NodeMigrationService(settings=Settings()).preflight_bundle(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
