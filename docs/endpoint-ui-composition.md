# Endpoint UI Composition

HexeVoice endpoint UI assets are SD-card driven. Firmware uses a layered scene manifest when present and falls back to the existing simple built-in screens when assets are missing or invalid.

## Folders

The endpoint creates and repairs these folders:

- `/sdcard/hexe/pictures`
- `/sdcard/hexe/sprites`
- `/sdcard/hexe/sounds`

Backgrounds and legacy full-screen state images live in `pictures`. Avatars, alpha masks, buttons, icons, scene manifests, and other overlays live in `sprites`. Audio files live in `sounds`.

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
    "error": {
      "filename": "avatar_error.rgb565",
      "transparent_rgb565": 63519,
      "width": 160,
      "height": 160,
      "x": 80,
      "y": 40
    }
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
    "idle": {
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
    "radius": 62,
    "color_rgb565": 65535,
    "date": true
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
- idle, muted, timer finished: `idle`
- listening: `listening`
- backend connecting or updating: `work`
- thinking: `thinking`
- replying: `talk`
- error: `error`

If a specific avatar key is missing, firmware falls back to `idle`. If no composited scene can be loaded, firmware falls back to the legacy full-screen SD UI assets and then to built-in simple drawings.

## Alpha Formats

Sprites and avatars support:

- `transparent_rgb565`: one color key is skipped while drawing.
- `alpha_format: "alpha8"`: one byte per pixel, `0` transparent and `255` opaque.
- `alpha_format: "alpha1"`: packed on/off transparency, eight pixels per byte.

`firmware/tools/convert_image.py --alpha-output` and `firmware/tools/convert-sprite.sh` can create RGB565 plus alpha mask files from a PNG with alpha.

## SD Media Reformat

The endpoint supports a media-only reformat command. This is not a partition format. It deletes files and subdirectories under:

- `/sdcard/hexe/pictures`
- `/sdcard/hexe/sprites`
- `/sdcard/hexe/sounds`

After deleting media entries, firmware recreates the three folders.
