# Hexe Firmware

This directory is the native standalone firmware track for Hexe.

It is intended to replace the ESPHome prototype over time while preserving the behavior documented in:

- [`docs/Expressif box.yaml`](/home/dan/Projects/HexeVoice/docs/Expressif%20box.yaml)
- [`docs/firmware-migration-plan.md`](/home/dan/Projects/HexeVoice/docs/firmware-migration-plan.md)
- [`docs/firmware-ota.md`](/home/dan/Projects/HexeVoice/docs/firmware-ota.md)

## Goals

- standalone Hexe device behavior
- direct ownership of UI, wake word, audio, networking, and OTA
- no required Home Assistant dependency

## Layout

- `main/`
  Native app entrypoint and modules
- `assets/`
  Shared firmware assets reference area

## Next Build Step

Once ESP-IDF is installed locally, the intended workflow is:

```bash
cd firmware
./build.sh
```

To copy the flashable artifacts into `firmware/export/` for another machine:

```bash
cd firmware
./export-artifacts.sh
```

This scaffold intentionally starts small. The first implementation goal is a branded boot screen, button handling, and board bring-up.
