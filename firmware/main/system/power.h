#pragma once

namespace hexe::system {

void init_power();
const char *power_runtime_mode();
bool power_low_power_mode_available();
bool power_shutdown_command_available();

}  // namespace hexe::system
