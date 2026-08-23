#pragma once

namespace hexe::voice {

void init_wake_word();
const char *wake_word_runtime_mode();
bool wake_word_on_device_available();
bool wake_word_backend_owned();

}  // namespace hexe::voice
