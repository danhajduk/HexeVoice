#pragma once

namespace hexe::voice {

void init_tts_player();
void play_listening_cue();
void handle_tts_ready(const char *stream_id, const char *content_type, const char *audio_url);
void stop_tts_playback();
void set_output_volume(int volume_percent);
bool tts_playback_active();

}  // namespace hexe::voice
