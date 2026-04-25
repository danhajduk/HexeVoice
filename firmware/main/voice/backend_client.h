#pragma once

#include <cstddef>
#include <cstdint>

namespace hexe::voice {

void init_backend_client();
bool submit_audio_frame(const int16_t *samples, size_t sample_count, uint32_t level, bool vad_speaking);
bool finish_audio_stream(const char *reason);
bool cancel_active_session(const char *reason);

}  // namespace hexe::voice
