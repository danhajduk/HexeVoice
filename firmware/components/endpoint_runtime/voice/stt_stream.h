#pragma once

namespace hexe::voice {

void init_stt_stream();
const char *stt_stream_runtime_mode();
bool stt_stream_local_decoder_available();
bool stt_stream_backend_owned();

}  // namespace hexe::voice
