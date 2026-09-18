# P4 UI Configuration

This directory contains the editable layout for the 1024x600 Waveshare P4 7B
display. Generate the device-facing JSON after every change:

```bash
python3 firmware/tools/generate-board-media-assets.py waveshare_p4_wifi6_touch_lcd_7b
```

The compiler validates references, expands presets and YAML includes, and writes
JSON to `assets/sprite/`. Do not edit the generated JSON directly.

## Files

- `status_layout.yaml`: compiler input index.
- `items.yaml`: reusable visual items and their data bindings.
- `animation_presets.yaml`: reusable animations with concrete parameters.
- `button_presets.yaml`: named ordered button collections.
- `screens_layout.yaml`: screen priority list using `!include`.
- `screens/*.yaml`: conditions and composition for one screen.

## Screen Preview

Open the interactive endpoint, screen, and duration menus:

```bash
scripts/ui-screen-menu.py
```

Use `m` to regenerate all P4 media or `n` to regenerate only configuration and
the manifest. Both actions request an asset sync from the selected endpoint.

Use the backend-owned `weather_test` screen as a placement sandbox. Its four
corner labels and center label are reusable items in `items.yaml`; edit their
center-based `x` and `y` coordinates, press `n`, and preview `weather_test`
again to find the desired weather-overlay positions.

The available durations are 5, 10, 20, and 30 seconds. A non-interactive call
is also supported:

```bash
scripts/ui-screen-menu.py --endpoint-id DEVICE_ID --screen idle --duration 10
```

The backend defaults to `http://hexe.local:9004`. Override it with
`API_BASE_URL` or `--api-base-url`. Use `--list-screens` to list screen IDs.

## Screens

Every screen declares who selects it with `owner: device` or `owner: backend`.
Device-owned screens are selected automatically from local conditions. A
backend-owned screen is eligible only after the backend selects it, and its
YAML conditions must also match. Screens are then checked from top to bottom;
the first matching screen wins. Keep `default` last.

The preview API is a temporary override and can show either ownership type:

```text
POST /api/endpoint/ui/screen        endpoint.ui.screen.render (temporary)
POST /api/endpoint/ui/screen/set    endpoint.ui.screen.render (persistent)
POST /api/endpoint/ui/screen/clear  endpoint.ui.screen.clear
```

The backend resolves the selected YAML into a compiled `layout` object and sends
the complete composition to one of two in-memory endpoint slots. The temporary
slot expires after `timeout_ms`; the persistent slot remains until replaced,
cleared, or the backend connection drops. The endpoint reports actual changes
with `endpoint.ui.screen.changed`. Every screen explicitly configures its
sidebars and buttons:

```yaml
id: listening
owner: device
conditions:
  match: all # all or any
  items:
    - flag: listening
      equals: true # optional; defaults to true
sidebars: true
buttons: {preset: standard}
items:
  - item: header_clock
  - item: activity_listening
    x: 512
    y: 295
```

Set `sidebars` to `false` for boot, connection, OTA, and error screens. Buttons
are rendered only when sidebars are enabled.

Buttons may use a preset or an explicit ordered list:

```yaml
buttons: {preset: standard}
```

```yaml
buttons:
  items:
    - button_timer
    - button_weather
    - button_config
```

Button items declare the exact intent they invoke with `intent_id`. The compiler
embeds both `id` and `intent_id` in every screen button declaration; firmware
returns both values in `endpoint.ui.button_pressed`, and the backend rejects
mismatched declarations before dispatching the registered intent. The timer
button invokes the HexeVoice-owned `timer.new` interaction intent;
`timer.create` remains reserved for the duration-bearing timer creation request.

Use `buttons: {preset: none}` for no buttons. Every referenced button must be a
`type: button` item in `items.yaml`.

Tapping a visible button sends an `endpoint.ui.button_pressed` WebSocket event.
Its payload contains `screen_id`, `button_id`, `button_index`, rendered `button`
bounds, the raw `touch` point, and `source: touch`. The list order determines
`button_index` and the vertical position within the configured button stack.

## Items

Each item has a unique `id`, a `type`, and optionally a `sprite`, `data`, text
style, dimensions, and animations. Screens reference the item ID and may set
its placement.

Supported types and data bindings:

- `header_clock`: `data: time`; centered 12-hour `HH:MM` header clock.
- `firmware_version`: `data: firmware_version`; firmware suffix.
- `big_clock`: `data: time`; clock frame, hours, separator, and minutes.
- `big_date`: `data: date_long` or `date_short`; uses its `format` value.
- `activity_sprite`: listening, thinking, replay, or timer artwork.
- `timer_primary`: `data: timer1`; earliest countdown and label.
- `timer_upcoming`: `data: timers_next`; subsequent timer rows.
- `progress_bar`: `data: ota_progress`.
- `status_icon_group`: shared status icon row coordinates.
- `status_icon`: fixed or floating status sprite.
- `sidebar`: left or right sidebar sprite and entrance animation.
- `button_stack`: button origin and vertical gap.
- `button`: a selectable sidebar button sprite.
- `text`: reusable literal or time/date text with configurable style and animation.

Text data bindings also include `system_message` for operational state such as
starting or connecting, and `error_message` for failure details. The endpoint
uses phase-based defaults when no custom message has been received. A backend
can set or clear either value with `endpoint.ui.message`:

```json
{
  "event_type": "endpoint.ui.message",
  "payload": {
    "system_message": "Connecting to Wi-Fi",
    "error_message": ""
  }
}
```

Colors use quoted `'#RRGGBB'` values. Coordinates are screen pixels. Font paths
are relative to `assets/font/`.

Every configurable `x` and `y` coordinate identifies the visual center of the
item. This applies to sprites, clock/date items and their text parts, timer
groups and rows, progress bars, status icons, firmware text, and sidebar
buttons. The renderer converts these centers to drawing origins. Generic text
supports `left`, `center`, or `right` alignment for compatibility, but its
placement remains center anchored:

```yaml
- id: welcome_label
  type: text
  text: Welcome home
  font: manrope/date_32.hxf
  font_size: 32
  color: '#D8FFFA'
  align: center
  x: 512
  y: 436
  animations:
    - preset: text_slide_up
      when: {flag: idle_ready, equals: true}
```

Add the item to any screen with `- item: welcome_label`. A screen placement can
override `x`, `y`, and `animations`. Use exactly one of `text` or `data`.
Supported dynamic bindings are `time`, `date_short`, and `date_long`; their
default `strftime` formats can be overridden with `format`.

The large clock and large date are separate items, so a screen can show either
one or both:

```yaml
items:
  - item: big_clock
    x: 512
    y: 310
  - item: big_date
    x: 512
    y: 35
```

Moving `big_clock` moves its frame and all three time components together.

## Status Icons

The `status_icons` group defines one shared `y` coordinate. Static icons use an
absolute `x`; floating icons begin at `floating.x`, use `floating.gap`, and
collapse left to right without empty slots.

```yaml
- id: wifi
  type: status_icon
  sprite: wifi
  placement: static # static or floating
  x: 900
```

## Animation Presets

Presets contain an animation type and exact parameters:

```yaml
presets:
  activity_slide_up:
    type: slide_in
    offset: {x: 0, y: 0.2}
    period_ms: 350
```

Reference a preset by name and add or override its activation condition:

```yaml
animations:
  - preset: activity_slide_up
    when: {flag: listening, equals: true}
```

Available animation types:

- `slide_in`: `offset.x`, `offset.y`, and `period_ms`.
- `blink_dot`: `color`, `position`, `radius`, and `period_ms`.
- `running_dots`: blink-dot fields plus `spacing` and `count` (1-8).
- `pulse`: `min_opacity`, `max_opacity` (0-255), and `period_ms`.
- `pulse_ring`: `color`, `position`, `radius`, and `period_ms`.
- `spinner`: rotating arc using `color`, `position`, `radius`, `thickness`, `arc`, and `period_ms`.
- `progress_ring`: progress arc using `color`, `position`, `radius`, and `thickness`. OTA progress is used with `ota_active` or `updating`; other flags use a repeating phase.
- `sweep`: horizontal highlight using `color`, `thickness`, and `period_ms`.
- `badge_ping`: badge dot with an expanding ring using `color`, `position`, `radius`, and `thickness`.
- `shake`: moves the owning sprite or text horizontally using `distance` and `period_ms`.
- `color_cycle`: interpolates a procedural ring through two to four `colors`; it also accepts `position`, `radius`, and `thickness`.

Offsets range from `-1.0` to `1.0`. Position, radius, and spacing are normalized
to the owning sprite. `thickness`, `arc`, and `distance` are normalized too.
Periods range from 100 to 60000 milliseconds.

Any animation can use a live audio level instead of its time-based phase:

```yaml
animations:
  - preset: mic_input_pulse
    when: {flag: listening}
    audio:
      source: mic_input # mic_input or speaker_output
      min_level: 200
      max_level: 6000
```

The level is normalized between `min_level` and `max_level`. A `pulse` maps it
to opacity, `pulse_ring` maps it to radius and brightness, `blink_dot` uses it
as an activity threshold and brightness, and `running_dots` maps it to the
active dot. Omit `audio` to retain the normal period-based animation. Audio
levels use the average absolute 16-bit PCM amplitude (0-32768).

## Conditions And Flags

Built-in flags are:

`heartbeat`, `loading`, `booting`, `updating`, `wifi_connected`,
`wifi_connecting`, `backend_connected`, `backend_connecting`,
`voice_ws_connected`, `asset_sync_active`, `media_transfer_active`,
`ota_active`, `listening`, `thinking`, `replying`, `muted`, `timer_active`,
`timer_finished`, `error`, `ui_ready`, `idle_ready`, `microphone_enabled`,
`microphone_disabled`, `microphone_active`, `dnd`, `alarm_active`,
`playback_active`, `update_available`, `warning_active`, `privacy_mode`, and
`cloud_offline`.

Screen conditions may use backend-provided custom UI flags. Animation conditions
must use a built-in flag.

## YAML Includes

`!include` paths are relative to the including file:

```yaml
screens:
  - !include screens/listening.yaml
  - !include screens/idle.yaml
  - !include screens/default.yaml
```

Includes may be nested but cannot leave this configuration directory. Missing
files and include cycles stop generation with an error.
