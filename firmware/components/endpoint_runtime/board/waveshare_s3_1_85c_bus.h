#pragma once

#include "driver/i2c_master.h"
#include "driver/i2s_std.h"

namespace hexe::board {

bool waveshare_185_init_i2c();
i2c_master_bus_handle_t waveshare_185_i2c_bus();
bool waveshare_185_reset_display();
bool waveshare_185_reset_touch();

bool waveshare_185_init_audio_i2s();
i2s_chan_handle_t waveshare_185_audio_rx_channel();
i2s_chan_handle_t waveshare_185_audio_tx_channel();

void waveshare_185_set_speaker_pa(bool enabled);

}  // namespace hexe::board
