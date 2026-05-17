# Neural TTS Engine Evaluation

Task 141 evaluated whether HexeVoice should add a local neural TTS engine beyond Piper, especially for CUDA-capable hosts.

## Current Baseline: Piper

Piper remains the best default for HexeVoice installs. It is fast, small, predictable, MIT-licensed, and already matches the current firmware contract: generate WAV, store an artifact sidecar, optionally write 16 kHz / 48 kHz variants, and let firmware fetch `endpoint_audio_url`.

The main caveat is project maintenance. The original `rhasspy/piper` repository is archived and read-only, with development pointing to a GPL successor. HexeVoice can keep using Piper as a runtime baseline, but should avoid tying future provider abstractions to Piper-specific assumptions.

Source: https://github.com/rhasspy/piper

## Candidate Comparison

| Engine | Fit | GPU value | License / maintenance | Install shape | HexeVoice impact |
| --- | --- | --- | --- | --- | --- |
| Piper | Best default | Low; CPU is already fast enough | MIT baseline, original repo archived | Existing Docker/socket runtime and ONNX voices | Keep as default |
| Kokoro 82M | Best experimental add | Medium; can run CPU, PyTorch/MPS paths exist, CUDA wrappers are common but not the core contract | Apache 2.0 model/code, active ecosystem | Small model around hundreds of MB, can be wrapped as HTTP/Unix socket service | Add optional provider behind existing TTS adapter shape |
| Coqui XTTS-v2 | Good quality/voice clone, poor default | High; supports CUDA inference | Coqui Public Model License, non-standard restrictions, original project/community situation is awkward | Larger PyTorch model, reference voice handling, more runtime state | Defer unless voice cloning becomes a product goal |
| StyleTTS2 | Research-quality, not install-friendly | Medium/high on modern GPUs | MIT code, but pretrained model rules and GPL-related inference dependency caveats | More complex dependencies, reference/style handling, no simple stable server contract | Do not implement before Kokoro |
| MeloTTS | Plausible multilingual option | Medium; model cards show cpu/cuda/mps device selection | MIT model cards, active enough but less aligned with current English-first node | Python service wrapper likely simple | Revisit after Kokoro if multilingual local voices matter |

Sources:

- Kokoro GitHub and model card: https://github.com/hexgrad/kokoro and https://huggingface.co/hexgrad/Kokoro-82M
- XTTS-v2 model card: https://huggingface.co/coqui/XTTS-v2
- StyleTTS2 GitHub: https://github.com/yl4579/StyleTTS2
- MeloTTS model card: https://huggingface.co/myshell-ai/MeloTTS-English-v3

## Recommendation

Keep Piper as the default install and migration target.

Add Kokoro as an optional experimental provider later, not as a replacement. The first implementation should be deliberately small:

- Dockerized `kokoro_tts` runtime with Unix socket support matching Piper.
- Request shape compatible with `PiperTextToSpeechAdapter`: text, optional voice, output WAV.
- Artifact path identical to the current TTS flow: raw WAV plus configured endpoint variants and sidecar metadata.
- Provider setup should expose voice/model selection and CPU/CUDA mode, but CPU should remain acceptable.
- CUDA should be treated as an optimization, not a hard dependency.

XTTS-v2 should not be added before Kokoro. It is useful for voice cloning and multilingual demonstrations, but its licensing and reference-audio workflow would force bigger UI, migration, privacy, and policy decisions.

## GPU TTS Decision

GPU TTS is worth supporting only as an optional provider profile. It is not worth blocking migration on. For HexeVoice’s current use case, STT benefits more from CUDA than TTS does because Piper already produces short command replies quickly on CPU.

The first GPU-capable TTS implementation should therefore be Kokoro experimental provider support with CPU fallback. Replace the default only after live latency, quality, model download size, and operational stability are better than Piper on the target host.
