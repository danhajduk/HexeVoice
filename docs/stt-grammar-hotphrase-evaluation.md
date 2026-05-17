# Constrained Grammar And Hot-Phrase STT Evaluation

Task 100 evaluated constrained grammar or hot-phrase recognition for common local commands.

## Current Position

HexeVoice already has a registered voice-intent layer after STT. That layer is the right authority for command semantics, but it still depends on the STT transcript being close enough for the matcher.

The best first step is not a full grammar decoder. It is hot-phrase biasing for likely local commands and entity names, paired with the existing intent-first STT fallback path.

## Options

- Hot phrases: pass common commands, room names, device names, and wake-adjacent words into the STT provider when supported.
- Constrained grammar: decode against a grammar for highly bounded commands such as timers, volume, mute, and local device actions.
- Post-STT correction: keep normal STT, then normalize known phrase confusions before intent matching.

## Recommendation

1. Add an intent registry export that returns active examples, matcher phrases, room/device aliases, and wake model names as `stt_hints`.
2. Send those hints to `external_faster_whisper` as an optional `hot_phrases` or `initial_prompt` payload field.
3. Keep constrained grammar as provider-specific and off by default until the command vocabulary is stable.
4. Use post-STT correction only for measured recurring mistakes, and store corrections beside intent metadata rather than hard-coding them in the pipeline.

## First Implementation Scope

- Add `hot_phrases` to the external STT request payload.
- Build hints from enabled voice intents and endpoint/core metadata.
- Record the hint count and source list in STT status and transcript timing metadata.
- Add tests that hints are sent without requiring any model download.

## Deferred Decisions

- Whether Core or the Voice Node owns room/device alias vocabulary.
- Whether grammar mode is global, per endpoint, or selected per intent family.
- Whether constrained grammar should bypass the normal assistant route or only improve transcript generation.
