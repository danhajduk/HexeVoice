#pragma once

#include <string>

namespace hexe::recovery {

void init_recovery_ble_provisioning();
bool recovery_ble_enabled();
bool recovery_ble_advertising();
const char *recovery_ble_state();
const char *recovery_ble_reason();
std::string render_recovery_ble_status_json();

}  // namespace hexe::recovery
