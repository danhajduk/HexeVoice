# P4 UI Configuration

These YAML files define the 1024x600 Waveshare P4 7B interface. Run the media
generator after editing them:

```bash
python3 firmware/tools/generate-board-media-assets.py waveshare_p4_wifi6_touch_lcd_7b
```

The generator resolves YAML includes and writes the device-facing JSON files to
`assets/sprite/`. Do not edit those generated JSON files directly.

## Files

- `status_layout.yaml`: ordered list of generated layout sections.
- `chrome_layout.yaml`: header clock, firmware version, sidebars, and buttons.
- `status_icons.yaml`: fixed and floating 40x40 status icons.
- `activity_layout.yaml`: activity sprite positions and animations.
- `idle_layout.yaml`: large idle clock, date, and timer countdown layout.
- `screens_layout.yaml`: screen priority list.
- `screens/*.yaml`: conditions and elements for one screen.

## YAML Includes

Use `!include` to split a YAML value into another file:

```yaml
screens:
  - !include screens/listening.yaml
  - !include screens/idle.yaml
  - !include screens/default.yaml
```

Paths are relative to the file containing the include. Nested includes are
supported. Includes cannot leave this configuration directory, and include
cycles are rejected.

## Common Values

- Coordinates are screen pixels unless an animation field says otherwise.
- Colors use quoted `'#RRGGBB'` strings.
- Fonts are paths relative to `assets/font/`.
- `font_size` is the requested pixel height and is scaled from the HXF font.
- `period_ms` accepts `100` through `60000` milliseconds.
- Conditions use `flag` and optional `equals`; `equals` defaults to `true`.

Supported built-in flags are:

`heartbeat`, `loading`, `booting`, `wifi_connected`, `wifi_connecting`,
`backend_connected`, `backend_connecting`, `voice_ws_connected`,
`asset_sync_active`, `media_transfer_active`, `ota_active`, `updating`,
`listening`, `thinking`, `replying`, `playback_active`, `muted`, `dnd`,
`microphone_enabled`, `microphone_disabled`, `microphone_active`,
`timer_active`, `timer_finished`, `alarm_active`, `update_available`,
`warning_active`, `privacy_mode`, `cloud_offline`, `error`, `ui_ready`, and
`idle_ready`.

Screen conditions may also use backend-provided custom UI flags. Animation
conditions must use one of the built-in flags above.

## Screen Selection

Screens are evaluated from top to bottom in `screens_layout.yaml`; the first
matching screen wins. Keep `default` last.

Each screen file supports:

```yaml
id: listening
conditions:
  match: all # all or any
  items:
    - flag: listening
      equals: true
elements:
  - type: clock
  - type: activity
    sprite: listening
```

Available element types:

- `clock`: small header clock.
- `idle_clock`: large clock and date from `idle_layout.yaml`.
- `activity`: activity sprite selected by `sprite`.
- `timer`: primary countdown and upcoming timers.
- `progress_bar`: rectangle configured with `x`, `y`, `width`, `height`,
  `color`, and `track_color`.

## Status Icons

`icons.y` is shared by every status icon. Static icons use a fixed `x`.
Floating icons start at `floating.x`, use `floating.gap`, and collapse left to
right with no empty slots when an icon is hidden.

```yaml
icons:
  y: 10
  floating:
    x: 30
    gap: 0
  items:
    - id: wifi
      placement: static # static or floating
      x: 900
      animations: []
```

Icon IDs correspond to the supported sprite assets and runtime states in
`status_icons.yaml`. Static icons require `x`; floating icons ignore it.

## Chrome

`clock` supports `font`, `font_size` (12-96), `color`, `x_offset`, and
`y_offset`. It is hidden while the large idle clock is visible.

`version` supports `font`, `font_size` (8-64), `color`, `x`, and `y`.

Each `sidebars.left` and `sidebars.right` entry is a `slide_in` animation.
`sidebar_buttons` supports `enabled`, `x`, `y`, and vertical `gap`.

## Idle Clock And Timers

`idle_clock.enabled` controls the whole composition. `date_format` uses
`strftime` formatting. The date is always centered horizontally; only its `y`
position is configurable.

The `sprite` block supports `x`, `y`, and `animations`. The `hours`,
`separator`, `minutes`, and `date` blocks support `font`, `font_size` (8-180),
`x`, `y`, `color`, and `animations`. The date ignores `x`.

`timer_screen.enabled` controls countdown rendering. `primary_countdown` and
`primary_label` accept text fields plus `x` and `y`. `upcoming` additionally
accepts `gap` (20-100) and `count` (1-3).

## Activity Sprites

Each `activity_sprites.items` entry supports `id`, `x`, `y`, and
`animations`. The ID selects the corresponding activity sprite loaded by the
firmware.

## Animations

Every animation supports:

```yaml
type: slide_in
when:
  flag: ui_ready
  equals: true
period_ms: 500
```

Available types and additional fields:

- `slide_in`: `offset.x`, `offset.y`; normalized signed sprite distances from
  `-1.0` to `1.0`.
- `blink_dot`: `color`, `position.x`, `position.y`, `radius`.
- `running_dots`: `color`, `position.x`, `position.y`, `radius`, `spacing`,
  and `count` (1-8).
- `pulse`: `min_opacity` and `max_opacity` (0-255).
- `pulse_ring`: `color`, `position.x`, `position.y`, and `radius`.

`position`, `radius`, and `spacing` are normalized to the owning sprite, so the
same animation can scale with different sprite sizes.
