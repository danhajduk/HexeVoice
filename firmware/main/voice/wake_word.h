#pragma once

namespace hexe::voice {

void init_wake_word();
const char *wake_word_runtime_mode();
bool wake_word_on_device_available();
bool wake_word_backend_owned();
const char *playback_stop_word_runtime_mode();
bool playback_stop_word_on_device_available();
bool playback_stop_word_active();
const char *playback_stop_word_unavailable_reason();

}  // namespace hexe::voice
