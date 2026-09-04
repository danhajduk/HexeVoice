#pragma once

#include <string>

namespace hexe::recovery {

void init_recovery_controls();
bool recovery_wifi_recovery_enabled();
bool recovery_full_http_rescue_enabled();
bool start_recovery_wifi_after_ble_credentials();
bool recovery_http_api_active();
const char *recovery_http_mode();
const char *recovery_network_mode();
const char *recovery_ip_address();
bool recovery_temporary_ap_active();
const char *recovery_discovery_status();
std::string render_partitions_json();
std::string render_diagnostics_json();

}  // namespace hexe::recovery
