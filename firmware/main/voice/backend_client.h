#pragma once

#include <cstddef>
#include <cstdint>

namespace hexe::voice {

void init_backend_client();
bool start_voice_session(const char *wake_source);
bool submit_audio_frame(const int16_t *samples, size_t sample_count, uint32_t level, bool vad_speaking);
bool finish_audio_stream(const char *reason);
bool cancel_active_session(const char *reason);
bool send_tts_playback_event(
    const char *event_type,
    const char *stream_id,
    const char *audio_url,
    const char *reason,
    size_t byte_count);

}  // namespace hexe::voice
