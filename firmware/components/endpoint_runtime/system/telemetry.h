#pragma once

namespace hexe::system {

void init_telemetry();
const char *telemetry_runtime_mode();
bool telemetry_dedicated_channel_enabled();
bool telemetry_heartbeat_owned();

}  // namespace hexe::system
