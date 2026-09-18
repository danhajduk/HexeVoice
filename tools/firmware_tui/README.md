# Hexe Firmware Console

Hexe Firmware Console is a Textual terminal application around the existing
firmware build scripts. It constructs argument-list commands, streams compiler
output, parses GCC and CMake diagnostics, and keeps reusable local profiles.

## Start

From the HexeVoice checkout:

```bash
.venv/bin/pip install -r requirements.txt
./tools/firmware_tui/run.sh
```

The launcher discovers buildable targets from `firmware/boards/*/board.yaml`.
Profiles are saved to `~/.config/hexevoice/firmware-tui.json`. The application
refuses to persist environment keys that look like credentials or secrets.

## Controls

- `b`: build
- `r`: rebuild in a fresh temporary build directory
- `c`: clean generated build directories for the selected target
- `F5`: build, then execute the profile's configured run command
- `x`: cancel the active compiler process
- `e`: focus diagnostics
- `o`: open the selected diagnostic in `$EDITOR`, VS Code, or `xdg-open`
- `p`: edit the active project/profile
- `t`: focus the target table
- `a`: edit extra compiler arguments
- `q`: quit

Additional arguments use shell-style quoting only for parsing into an argument
list. Compiler processes are launched without `shell=True`. Full ANSI compiler
lines are rendered through Rich's ANSI parser, while diagnostics use explicit
severity colors.

See `sample-profile.json` for the persisted configuration shape.
