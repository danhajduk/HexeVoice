#pragma once

#include <cstdint>
#include <ctime>
#include <string>

namespace hexe::system {

void sync_clock_from_server(int64_t server_unix_ms, int32_t utc_offset_seconds, int64_t round_trip_us);
bool clock_synced();
bool current_local_time(std::tm *local_time);
bool current_utc_unix_ms(int64_t *utc_ms);
int current_local_minute_signature();
std::string current_utc_timestamp();

}  // namespace hexe::system
