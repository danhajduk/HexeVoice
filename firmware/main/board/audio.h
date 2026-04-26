#pragma once

namespace hexe::board {

void init_audio();
void update_audio();
bool pause_microphone_for_playback();
void resume_microphone_after_playback();

}  // namespace hexe::board
