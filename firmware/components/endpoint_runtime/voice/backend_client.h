#pragma once

#include <cstddef>
#include <cstdint>

namespace hexe::voice {

struct MicroVadFrameState {
  uint32_t chunk_index{0};
  bool active{false};
  bool started{false};
  bool ended{false};
  uint32_t pause_ms{0};
};

struct WakeCandidateMetrics {
  const char *source{nullptr};
  const char *model{nullptr};
  float confidence{0.0f};
  uint32_t chunk_index{0};
  uint32_t chunk_count{0};
  uint32_t detection_window_ms{0};
  uint32_t frame_level{0};
  uint32_t noise_floor_level{0};
  uint32_t speech_peak_level{0};
  const char *endpoint_audio_profile_version{nullptr};
};

void init_backend_client();
bool start_voice_session(const char *wake_source);
bool notify_vad_speech_started(uint32_t level);
bool post_tts_input_cooldown_active();
bool submit_wake_candidate(const WakeCandidateMetrics &candidate);
bool submit_audio_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking,
    const MicroVadFrameState *micro_vad = nullptr);
void observe_passive_placement_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    bool speech_like_activity);
bool finish_audio_stream(const char *reason);
bool cancel_active_session(const char *reason);
bool send_tts_playback_event(
    const char *event_type,
    const char *stream_id,
    const char *audio_url,
    const char *reason,
    size_t byte_count);

}  // namespace hexe::voice
