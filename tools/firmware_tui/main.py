from __future__ import annotations

import argparse
from pathlib import Path

from .tui_app import FirmwareBuildApp


def main() -> None:
    parser = argparse.ArgumentParser(description="Hexe Textual firmware compiler wrapper")
    parser.add_argument("project", nargs="?", type=Path, default=Path.cwd(), help="HexeVoice project directory")
    args = parser.parse_args()
    FirmwareBuildApp(args.project).run()


if __name__ == "__main__":
    main()
