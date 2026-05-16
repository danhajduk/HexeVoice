#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from hexevoice.api.models import NodeMigrationBackupRequest, NodeMigrationRestoreRequest  # noqa: E402
from hexevoice.config.settings import Settings  # noqa: E402
from hexevoice.migration import NodeMigrationService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or restore HexeVoice migration backups.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("create", help="Create a timestamped migration backup.")
    backup.add_argument("--label")

    restore = subparsers.add_parser("restore", help="Restore or dry-run a migration backup.")
    restore.add_argument("backup_id")
    restore.add_argument("--dry-run", action="store_true")
    restore.add_argument("--core-url", dest="destination_core_base_url")
    restore.add_argument("--api-url", dest="destination_api_base_url")
    restore.add_argument("--ui-url", dest="destination_ui_endpoint")
    restore.add_argument("--hostname", dest="destination_hostname")

    args = parser.parse_args()
    service = NodeMigrationService(settings=Settings())
    if args.command == "create":
        result = service.create_backup(
            NodeMigrationBackupRequest(
                label=args.label,
            )
        )
    else:
        result = service.restore_backup(
            NodeMigrationRestoreRequest(
                backup_id=args.backup_id,
                dry_run=args.dry_run,
                destination_core_base_url=args.destination_core_base_url,
                destination_api_base_url=args.destination_api_base_url,
                destination_ui_endpoint=args.destination_ui_endpoint,
                destination_hostname=args.destination_hostname,
            )
        ).model_dump(mode="json")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
