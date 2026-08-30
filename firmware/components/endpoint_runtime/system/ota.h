#pragma once

#include <cstddef>

namespace hexe::system {

struct OtaUpdateManifest {
  const char *request_id;
  const char *url;
  const char *version;
  const char *profile;
  const char *sha256;
  int size_bytes;
  const char *signature_algorithm;
  const char *signature_key_id;
  const char *manifest_signature;
};

void init_ota();
bool start_ota_update(const OtaUpdateManifest &manifest, char *error_code, size_t error_code_size);

}  // namespace hexe::system
