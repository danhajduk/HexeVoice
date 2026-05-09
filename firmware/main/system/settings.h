#pragma once

namespace hexe::system {

void init_settings();
void set_muted(bool muted);
void set_output_volume_percent(int volume_percent);
int micro_vad_pause_ms();
void set_micro_vad_pause_ms(int pause_ms);

}  // namespace hexe::system
