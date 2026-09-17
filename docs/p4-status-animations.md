# P4 Status Animations

The Waveshare P4 7B header supports reusable procedural animations configured
under `firmware/assets/waveshare_p4_wifi6_touch_lcd_7b/sprites/`. The repository
keeps the editable layout configuration as YAML. Running
`firmware/tools/generate-board-media-assets.py waveshare_p4_wifi6_touch_lcd_7b`
converts it to the JSON files consumed from the SD card. `status_layout.yaml`
lists the independently editable sections by their generated JSON names:

- `chrome_layout.yaml`: header clock, firmware version, sidebars, and buttons
- `status_icons.yaml`: static and floating status icons
- `activity_layout.yaml`: listening, thinking, reply, and timer sprites
- `idle_layout.yaml`: large idle clock and timer countdown screen
- `screens_layout.yaml`: ordered screen index using `!include`
- `screens/*.yaml`: one status condition and its ordered elements per screen

The media generator converts the YAML index and its five sections into JSON in
the board asset library.
Firmware merges the listed sections in order and reloads them after asset
synchronization. A legacy monolithic `status_layout.json` remains supported.

Screen priority stays visible in the small index file while each screen can be
tuned independently:

```yaml
schema_version: 1
screens:
  - !include screens/updating.yaml
  - !include screens/timer.yaml
  - !include screens/idle.yaml
  - !include screens/default.yaml
```

An include path is resolved relative to the file containing it. Includes may
be nested, but they cannot escape the top-level YAML file's directory and
cycles are rejected. The media generator resolves all includes before writing
the single `screens_layout.json` file consumed by the firmware.

## Coordinate Model

The layout has one shared screen `y` coordinate. Static icons define an absolute
screen `x`; floating icons use the shared `floating.x` origin and render in JSON
order with `floating.gap` pixels between active icons.

Animation geometry is relative to the owning sprite:

- `position.x` and `position.y`: `0.0` to `1.0` across the sprite.
- `radius`: `0.0` to `1.0` of the sprite's shorter edge.
- `spacing`: `0.0` to `1.0` of the sprite's shorter edge.

This lets one animation definition scale with 40x40 or larger sprites.

## Available Animations

### `blink_dot`

Draws a dot for half of each period. Parameters: `color`, `position`, `radius`,
and `period_ms`.

### `running_dots`

Draws up to eight horizontal dots and advances the highlighted dot. Parameters:
`color`, `position`, `count`, `radius`, `spacing`, and `period_ms`.

### `pulse`

Modulates the owning sprite's opacity with a smooth triangular cycle.
Parameters: `period_ms`, `min_opacity`, and `max_opacity` (0-255).

### `pulse_ring`

Expands and dims a procedural ring around a relative point. Parameters:
`color`, `position`, `radius`, and `period_ms`.

### `slide_in`

Moves the owning sprite into its configured position once when the condition
becomes true, then holds it in place. `offset.x` and `offset.y` are normalized
signed distances from `-1.0` to `1.0`, relative to the sprite dimensions.
`period_ms` controls the transition duration. For example, an icon entering
from one sprite-width to the left uses:

```yaml
type: slide_in
when:
  flag: asset_sync_active
  equals: true
offset:
  x: -1.0
  y: 0.0
period_ms: 350
```

Every animation requires a `when` object:

```yaml
when:
  flag: asset_sync_active
  equals: true
```

## Device Flags

Supported flags are `heartbeat`, `loading`, `wifi_connected`,
`wifi_connecting`, `backend_connected`, `backend_connecting`,
`voice_ws_connected`, `asset_sync_active`, `media_transfer_active`,
`ota_active`, `listening`, `thinking`, `replying`, `muted`, `timer_active`,
`timer_finished`, `error`, and `ui_ready`.

`heartbeat` is always true while the display runtime is alive. `loading` combines
boot, Wi-Fi connection, backend connection, asset synchronization, and OTA.
`ui_ready` becomes true only after Wi-Fi and backend connection complete, while
OTA is inactive and the application is not updating or in an error state. The
left and right sidebar sprites use this flag to slide into view.

`idle_ready` additionally requires synchronized time and the application idle
phase. It controls the large idle clock composition. While active, the large
clock replaces the small header clock. Its frame, hours, separator, minutes,
and date each have independent animation lists in `idle_layout.yaml`.

The optional `timer_screen` object keeps that idle clock visible while timers
are active. `primary_countdown` and `primary_label` configure the earliest
timer at the lower left. `upcoming` configures the lower-right list and accepts
`x`, `y`, `gap`, and `count` (up to three), plus the standard `font`,
`font_size`, and `color` text fields. Countdown values are derived locally from
the timer deadline and redraw once per second.

## Deferred Animations

Candidates for later implementation:

- `spinner`: rotating arc for indeterminate work.
- `progress_ring`: numeric progress from OTA or asset synchronization.
- `sweep`: directional highlight for scanning and discovery.
- `badge_ping`: one-shot notification badge entrance.
- `shake`: short error or rejected-action movement.
- `color_cycle`: interpolation through a configured color list.

Future animation types should retain the normalized coordinate model, use the
same condition flags, and avoid introducing animation-specific device state.
