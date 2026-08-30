#pragma once

#include <string>

namespace hexe::recovery {

void init_recovery_controls();
bool recovery_http_api_active();
const char *recovery_network_mode();
const char *recovery_ip_address();
bool recovery_temporary_ap_active();
std::string render_partitions_json();
std::string render_diagnostics_json();

}  // namespace hexe::recovery
