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
const char *ota_boot_validation_status();
const char *ota_boot_validation_error();
const char *ota_running_partition_label();
const char *ota_running_partition_state();
bool ota_boot_pending_verification();
bool ota_boot_self_tests_passed();
bool ota_boot_marked_valid();
bool ota_rollback_available();

}  // namespace hexe::system
