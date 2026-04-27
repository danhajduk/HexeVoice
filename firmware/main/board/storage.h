#pragma once

namespace hexe::board {

void init_storage();
bool sd_card_mounted();
bool ensure_sd_media_directories();
const char *sd_card_mount_path();
const char *sd_card_pictures_path();
const char *sd_card_sprites_path();
const char *sd_card_sounds_path();

}  // namespace hexe::board
