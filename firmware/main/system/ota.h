#pragma once

namespace hexe::system {

void init_ota();
bool start_ota_update(const char *url, const char *version, const char *sha256, int size_bytes);

}  // namespace hexe::system
