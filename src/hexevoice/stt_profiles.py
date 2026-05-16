from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from typing import Any
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hexevoice.config.settings import Settings
    from hexevoice.voice.pipeline import SpeechTranscript


@dataclass(frozen=True)
class SttModelProfile:
    name: str
    model: str
    device: str
    compute_type: str
    preload: bool = True
    auto_download: bool = True
    language: str | None = "en"
    beam_size: int | None = 5
    best_of: int | None = 5
    without_timestamps: bool = True
    word_timestamps: bool = False
    max_initial_timestamp: float | None = 1.0
    fallback_profile: str | None = None
    fallback_when: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


STT_MODEL_PROFILES: dict[str, SttModelProfile] = {
    "cpu_default": SttModelProfile(
        name="cpu_default",
        model="base.en",
        device="cpu",
        compute_type="int8",
    ),
    "cuda_fast_intent": SttModelProfile(
        name="cuda_fast_intent",
        model="small.en",
        device="cuda",
        compute_type="float16",
        beam_size=1,
        best_of=1,
        fallback_profile="cuda_accurate_fallback",
        fallback_when=("low_confidence", "empty_transcript", "intent_unmatched"),
    ),
    "cuda_accurate_fallback": SttModelProfile(
        name="cuda_accurate_fallback",
        model="medium.en",
        device="cuda",
        compute_type="float16",
        beam_size=5,
        best_of=5,
    ),
}


def stt_profile_options() -> list[dict[str, str]]:
    return [{"value": name, "label": name.replace("_", " ").title()} for name in STT_MODEL_PROFILES]


def get_stt_model_profile(name: str) -> SttModelProfile:
    normalized = str(name or "").strip()
    try:
        return STT_MODEL_PROFILES[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown_stt_profile:{normalized}") from exc


def resolve_stt_model_profile(
    settings: "Settings",
    provider_config: dict[str, Any] | None = None,
) -> SttModelProfile:
    provider_config = provider_config or {}
    profile_name = str(provider_config.get("profile") or getattr(settings, "voice_stt_profile", "") or "").strip()
    fallback_profile = str(
        provider_config.get("fallback_profile") or getattr(settings, "voice_stt_fallback_profile", "") or ""
    ).strip()

    if profile_name:
        profile = get_stt_model_profile(profile_name)
    else:
        profile = SttModelProfile(
            name="custom_legacy",
            model=getattr(settings, "voice_stt_faster_whisper_model"),
            device=getattr(settings, "voice_stt_faster_whisper_device"),
            compute_type=getattr(settings, "voice_stt_faster_whisper_compute_type"),
            preload=bool(getattr(settings, "voice_stt_preload")),
            language=getattr(settings, "voice_stt_faster_whisper_language"),
            beam_size=getattr(settings, "voice_stt_faster_whisper_beam_size"),
            best_of=getattr(settings, "voice_stt_faster_whisper_best_of"),
            without_timestamps=bool(getattr(settings, "voice_stt_faster_whisper_without_timestamps")),
            word_timestamps=bool(getattr(settings, "voice_stt_faster_whisper_word_timestamps")),
            max_initial_timestamp=getattr(settings, "voice_stt_faster_whisper_max_initial_timestamp"),
            fallback_profile=fallback_profile or None,
        )

    overrides: dict[str, Any] = {}
    for key in ("model", "device", "compute_type", "language"):
        value = str(provider_config.get(key) or "").strip()
        if value:
            overrides[key] = value
    for key in ("beam_size", "best_of"):
        if provider_config.get(key) is not None:
            overrides[key] = int(provider_config[key])
    if provider_config.get("warm_model") is not None:
        overrides["preload"] = bool(provider_config.get("warm_model"))
    if fallback_profile:
        get_stt_model_profile(fallback_profile)
        overrides["fallback_profile"] = fallback_profile
    if not overrides:
        return profile
    return SttModelProfile(**{**profile.as_dict(), **overrides})


def should_use_stt_fallback(
    transcript: "SpeechTranscript",
    *,
    profile: SttModelProfile,
    intent_matched: bool,
    min_confidence: float = 0.55,
    min_text_chars: int = 3,
) -> bool:
    if not profile.fallback_profile:
        return False
    text = (transcript.text or "").strip()
    reasons = set(profile.fallback_when)
    if transcript.error:
        return True
    if "empty_transcript" in reasons and len(text) < min_text_chars:
        return True
    if "low_confidence" in reasons and transcript.confidence is not None and transcript.confidence < min_confidence:
        return True
    if "intent_unmatched" in reasons and text and not intent_matched:
        return True
    return False
