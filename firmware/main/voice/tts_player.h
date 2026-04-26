#pragma once

namespace hexe::voice {

void init_tts_player();
void handle_tts_ready(const char *stream_id, const char *content_type, const char *audio_url);
void stop_tts_playback();
bool tts_playback_active();

}  // namespace hexe::voice
