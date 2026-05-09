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

void init_backend_client();
bool start_voice_session(const char *wake_source);
bool notify_vad_speech_started(uint32_t level);
bool submit_audio_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    bool vad_speaking,
    const MicroVadFrameState *micro_vad = nullptr);
bool finish_audio_stream(const char *reason);
bool cancel_active_session(const char *reason);
bool send_tts_playback_event(
    const char *event_type,
    const char *stream_id,
    const char *audio_url,
    const char *reason,
    size_t byte_count);

}  // namespace hexe::voice
