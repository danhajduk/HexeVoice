#pragma once

namespace hexe::board {

void init_storage();
bool sd_card_mounted();
const char *sd_card_mount_path();
const char *sd_card_pictures_path();
const char *sd_card_sounds_path();

}  // namespace hexe::board
