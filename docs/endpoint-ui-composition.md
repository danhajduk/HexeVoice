# Endpoint UI Composition

HexeVoice endpoint UI assets are SD-card driven. Firmware uses a layered scene manifest for pictures, avatars, and sprites. If the manifest is missing or invalid, firmware leaves the display unchanged instead of drawing a fallback. If a referenced background, logo, avatar, or sprite file is missing, firmware skips that layer and continues with the next one.

## Folders

The endpoint creates and repairs these folders:

- `/sdcard/hexe/pictures`
- `/sdcard/hexe/sprites`
- `/sdcard/hexe/sounds`

Backgrounds live in `pictures`. Avatars, alpha masks, buttons, icons, scene manifests, and other overlays live in `sprites`. Audio files live in `sounds`.

Layer `width` and `height` describe the actual RGB565 and alpha-mask file dimensions. `x` and `y` place that layer on the LCD. Set `"clip": true` on a layer to allow it to be partially off-screen; firmware clips it while drawing. This lets a full `320x240` avatar use `"y": 5` to move the art down while the bottom 5 pixels are clipped. Without `clip`, off-screen geometry is ignored so accidental bad positions do not draw.

## Manifest

The first composited UI manifest is:

`/sdcard/hexe/sprites/ui_manifest.json`

Minimal voice-scene example:

```json
{
  "schema_version": 1,
  "type": "avatar",
  "background": "Default.rgb565",
  "avatars": {
    "idle": {
      "filename": "avatar_idle.rgb565",
      "alpha": "avatar_idle.alpha8",
      "alpha_format": "alpha8",
      "width": 160,
      "height": 160,
      "x": 80,
      "y": 40
    },
    "thinking": {
      "filename": "avatar_thinking.rgb565",
      "alpha": "avatar_thinking.alpha8",
      "alpha_format": "alpha8",
      "width": 160,
      "height": 160,
      "x": 80,
      "y": 40
    },
    "clock": {
      "filename": "clock_face.rgb565",
      "alpha": "clock_face.alpha8",
      "alpha_format": "alpha8",
      "width": 180,
      "height": 180,
      "x": 70,
      "y": 20
    },
    "ota": {
      "filename": "ota.rgb565",
      "width": 320,
      "height": 240,
      "x": 0,
      "y": 0
    },
    "error": {
      "filename": "avatar_error.rgb565",
      "transparent_rgb565": 63519,
      "width": 160,
      "height": 160,
      "x": 80,
      "y": 40
    }
  },
  "clock": {
    "cx": 160,
    "cy": 110,
    "hands_dx": 0,
    "hands_dy": 0,
    "radius": 62,
    "hour_radius_percent": 50,
    "minute_radius_percent": 75,
    "seconds": true,
    "second_radius_percent": 82,
    "color_rgb565": 65535,
    "second_color_rgb565": 63488,
    "frame": false,
    "date": true,
    "date_split": true,
    "day_x": 92,
    "day_y": 202,
    "day_scale_percent": 200,
    "day_format": "short",
    "date_x": 136,
    "date_y": 202,
    "date_scale_percent": 200
  },
  "ota_progress": {
    "enabled": true,
    "frame": true,
    "orientation": "horizontal",
    "x": 58,
    "y": 205,
    "width": 204,
    "height": 12,
    "padding": 2,
    "shadow_margin": 3,
    "shadow_color_rgb565": 0,
    "background_color_rgb565": 6339,
    "outline_color_rgb565": 65535,
    "fill_color_rgb565": 2047
  },
  "sprites": [
    {
      "id": "settings",
      "filename": "settings.rgb565",
      "alpha": "settings.alpha8",
      "alpha_format": "alpha8",
      "width": 32,
      "height": 32,
      "x": 280,
      "y": 200,
      "touch": { "x": 270, "y": 190, "width": 50, "height": 50 }
    }
  ]
}
```

Clock-scene example:

```json
{
  "schema_version": 1,
  "type": "clock",
  "background": "ClockBg.rgb565",
  "avatars": {
    "clock": {
      "filename": "clock_face.rgb565",
      "alpha": "clock_face.alpha8",
      "alpha_format": "alpha8",
      "width": 180,
      "height": 180,
      "x": 70,
      "y": 20
    }
  },
  "clock": {
    "cx": 160,
    "cy": 110,
    "hands_dx": 0,
    "hands_dy": 0,
    "radius": 62,
    "hour_radius_percent": 50,
    "minute_radius_percent": 75,
    "seconds": true,
    "second_radius_percent": 82,
    "color_rgb565": 65535,
    "second_color_rgb565": 63488,
    "frame": false,
    "date": true,
    "date_split": true,
    "day_x": 92,
    "day_y": 202,
    "day_scale_percent": 200,
    "day_format": "short",
    "date_x": 136,
    "date_y": 202,
    "date_scale_percent": 200
  }
}
```

## Layer Order

Firmware renders layers in this order:

1. Background from `/sdcard/hexe/pictures`.
2. Current avatar variant from `/sdcard/hexe/sprites`.
3. Scene-specific dynamic overlays such as clock hands and date.
4. Manifest sprites such as buttons and icons.
5. Built-in status overlays such as Wi-Fi, audio streaming, volume, and OTA progress.

## Avatar States

The firmware maps endpoint phases to avatar keys:

- booting or Wi-Fi connecting: `logo`
- idle: `clock`
- muted or timer finished: `idle`
- listening: `listening`
- backend connecting or updating: `work`
- thinking: `thinking`
- replying: `talk`
- error: `error`
- OTA update active: `ota`

If a specific avatar key is missing, that layer is skipped; firmware does not substitute `idle`. If no composited scene manifest can be loaded, the display is left unchanged.
Clock hands and the optional date are drawn only when the selected avatar is `clock`.
The legacy `clock.idle_timeout_ms` manifest field is still accepted by firmware for older SD-card media, but idle now selects the clock immediately.

Clock overlay options:

- `frame`: draw the old square clock frame when `true`; default is `false`.
- `cx`, `cy`, and `radius`: clock face center and radius used for hand length.
- `hands_dx` and `hands_dy`: pixel offsets applied only to the drawn hands, useful when the art center is slightly off.
- `hour_radius_percent` and `minute_radius_percent`: hand lengths as a percentage of `radius`.
- `seconds`: draw a seconds hand when `true`; when enabled, the clock redraws once per second.
- `second_radius_percent`: seconds hand length as a percentage of `radius`.
- `second_color_rgb565`: RGB565 color for the seconds hand; default is red (`63488`).
- `date`: draws a date when `true`.
- `date_split`: when `true`, draws the weekday and date separately, for example `Mon` and `Apr 27`.
- `day_x`: weekday text x position. Use `-1` to center it automatically.
- `day_y`: weekday text y position.
- `day_scale_percent`: weekday bitmap text scale; `100` is small, `180` is 1.8x, and `200` is the default.
- `day_format`: `short` draws `Mon`; `long` draws `Monday`.
- `day_text`: optional weekday override for layout testing, for example `Wednesday`; remove it to use the real weekday.
- `date_x`: date text x position. Use `-1` to center it automatically. In split mode this controls only the month/day text.
- `date_y`: date text y position.
- `date_scale_percent`: date bitmap text scale; `100` is small, `150` is 1.5x, and `200` is the default.

OTA progress options:

- `enabled`: draws the OTA progress bar when `true`; default is `true`.
- `frame`: draws the shadow, background, and outline when `true`; when `false`, only the fill is drawn.
- `orientation`: `horizontal` or `vertical`; default is `horizontal`.
- `x`, `y`, `width`, and `height`: progress bar geometry.
- `padding`: inset from the bar outline to the fill area.
- `shadow_margin`: shadow rectangle margin around the bar.
- `shadow_color_rgb565`, `background_color_rgb565`, `outline_color_rgb565`, and `fill_color_rgb565`: RGB565 colors.

## Alpha Formats

Sprites and avatars support:

- `transparent_rgb565`: one color key is skipped while drawing.
- `alpha_format: "alpha8"`: one byte per pixel, `0` transparent and `255` opaque.
- `alpha_format: "alpha1"`: packed on/off transparency, eight pixels per byte.

`firmware/tools/convert_image.py --alpha-output` and `firmware/tools/convert-sprite.sh` can create RGB565 plus alpha mask files from a PNG with alpha. `convert-sprite.sh` also treats exact `#FF00FF` pixels as transparent by default; set `ALPHA_COLOR=` to disable that color key or set `ALPHA_COLOR=#RRGGBB` to use another key.

`firmware/assets/converted_files/hexe/sprites/push-sprite.sh SPRITE_NAME` uploads the named sprite from `ui_manifest.json`. It uploads the RGB565 file and, when declared or present beside it, the matching `alpha8` or `alpha1` mask.

## SD Media Reformat

The endpoint supports a media-only reformat command. This is not a partition format. It deletes files and subdirectories under:

- `/sdcard/hexe/pictures`
- `/sdcard/hexe/sprites`
- `/sdcard/hexe/sounds`

After deleting media entries, firmware recreates the three folders.
