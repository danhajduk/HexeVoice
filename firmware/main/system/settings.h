#pragma once

namespace hexe::system {

struct EndpointProvisioningSettings {
  char endpoint_id[64];
  char display_name[64];
  char backend_host[96];
  int http_port;
  int ws_port;
  bool use_tls;
  char wifi_ssid[33];
  char wifi_password[65];
  bool configured;
};

void init_settings();
void set_muted(bool muted);
void set_output_volume_percent(int volume_percent);
int micro_vad_pause_ms();
void set_micro_vad_pause_ms(int pause_ms);
const EndpointProvisioningSettings &endpoint_provisioning_settings();
const char *endpoint_id();
const char *endpoint_display_name();
const char *endpoint_backend_host();
int endpoint_http_port();
int endpoint_ws_port();
bool endpoint_use_tls();
const char *wifi_ssid();
const char *wifi_password();
bool provisioning_configured();
bool save_endpoint_provisioning(const EndpointProvisioningSettings &settings);
void reset_endpoint_provisioning();

}  // namespace hexe::system
