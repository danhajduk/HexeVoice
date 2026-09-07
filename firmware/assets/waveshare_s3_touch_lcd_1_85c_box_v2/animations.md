# Waveshare 1.85 Round UI Animations

Target display: 360 x 360 round LCD, 262K colors.

These PNGs are intended to be static base plates. Firmware should draw small,
procedural overlays on top instead of storing full animation frame sequences.
That keeps flash usage low, makes timing flexible, and lets live data such as
clock time or audio level drive the UI.

## General Pattern

- Blit the state PNG as the background.
- Draw lightweight overlays in code.
- Keep important drawing inside a circular safe radius of about 165 px.
- Prefer 15-20 FPS for active states.
- Use lower frame rates or timer-only updates for idle/error states.
- Convert final assets to RGB565 for firmware use.
- Use dirty rectangles when practical; otherwise full-frame redraw should be
  acceptable if QSPI bandwidth remains comfortable.

## State Animations

### Idle

Base assets:

- `idle_clock_face.png`
- `idle.png`

- Draw hour and minute hands in code.
- Optional: draw a tiny second dot instead of a full second hand.
- Pulse the outer cyan ring very subtly every few seconds.
- Keep the center cap small so the hands remain the primary live element.
- Prefer `idle.png` when drawing status icons or the
  current date on the clock face.

Recommended overlay regions for `idle.png`:

- Status icon rail: `x=104, y=44, w=152, h=30`
- Status icon centers: `(127,59)`, `(153,59)`, `(180,59)`, `(207,59)`,
  `(233,59)`
- Date capsule: `x=136, y=250, w=88, h=30`
- Date text center: `(180,265)`
- Date format: `MM/DD`
- Draw order: base image, clock hands, date text, status icons.

### Listen

Base asset: `listening.png`

- Pulse the central orb while listening.
- Expand short cyan arcs outward from the center.
- If microphone amplitude is available, map it to orb size and arc brightness.
- Keep motion symmetric and calm so it reads as attention, not output.
- Implementation: keep `listening.png` as a quiet base plate. Firmware caches
  the RGB565 base in PSRAM, restores the center dirty rectangle, draws blended
  orb/arc overlays, and flushes only that region for animation ticks.

### Think

Base asset: `thinking.png`

- Rotate the segmented arcs slowly.
- Pulse the three center dots in sequence.
- Use timer-driven motion only; no audio input is needed.
- Keep brightness below the talk state so the state hierarchy stays clear.
- Implementation: keep `thinking.png` as a quiet base plate and use the shared
  animated status overlay path to restore its dirty rectangle, draw rotating
  arc segments plus sequenced dots, and partially flush the center area.

### Talk

Base asset: `replying.png`

- Animate the side waveform bars while TTS/audio output is active.
- If output amplitude is available, map it to bar height and brightness.
- If amplitude is not available, use a procedural sine/noise pattern.
- Keep the center core steady enough that the screen does not feel frantic.
- Implementation: keep `replying.png` as a quiet base plate with empty side
  lanes. Firmware uses the shared animated overlay path to draw procedural
  vertical waveform bars and a restrained center pulse.

### Work

Base asset: `connecting.png`

- Rotate or step the segmented progress rails.
- Pulse the central module nodes while an action is running.
- On completion, briefly brighten the central check shape.
- Transition back to idle after the completion flash or next voice state.

### Error

Base asset: `error.png`

- Slowly pulse the warning triangle glow.
- Optionally flicker broken ring segments at a very low rate.
- Avoid fast flashing.
- Keep the screen readable and calm so it signals a recoverable problem.

## Transitions

Recommended transition duration: 150-250 ms.

- Idle to listen: cyan radial wake sweep.
- Listen to think: collapse listening arcs into center dots.
- Think to talk: expand dots into side waveform bars.
- Talk to work: fade waveform bars into progress rails.
- Any state to error: quick magenta ring sweep, then settle into slow pulse.
- Any complete state to idle: short fade or outer-ring sweep.

## Implementation Notes

- Store one base asset per state.
- Keep overlay renderers state-specific.
- Use lookup tables or fixed-point math if arc rendering becomes expensive.
- Clamp overlay brightness for RGB565 so cyan/violet gradients do not band too
  aggressively.
- Keep all text out of these assets; state labels belong in logs or companion UI,
  not on the round display.

## Layered Asset Direction

- Keep `idle.rgb565` as the full-screen idle/clock background.
- Use `active.rgb565` as the shared full-screen non-idle background.
- Keep the existing full-screen status pictures under `assets/picture/` for
  fallback and visual reference.
- Store status mark overlays under `assets/sprite/` as 128 x 128 RGB565 sprites
  with matching alpha8 masks.
- Draw motion procedurally on top of the active background and status sprite.
