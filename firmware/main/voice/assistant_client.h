#pragma once

namespace hexe::voice {

void init_assistant_client();
const char *assistant_client_runtime_mode();
bool assistant_client_local_llm_available();
bool assistant_client_backend_owned();

}  // namespace hexe::voice
