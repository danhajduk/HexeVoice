#pragma once

#include <string>

namespace hexe::recovery {

void init_recovery_runtime();
std::string render_status_json();
void log_recovery_status();

}  // namespace hexe::recovery
