#include "system/log_stream.h"

#include <cstdarg>
#include <cstdio>
#include <cstring>

#include "endpoint_config.h"
#include "esp_log.h"
#include "esp_log_write.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"

namespace {
constexpr char kTag[] = "hexe_log_stream";
constexpr int kLogLineMaxBytes = 512;

vprintf_like_t g_serial_vprintf = nullptr;
int g_udp_socket = -1;
sockaddr_in g_udp_destination = {};
bool g_udp_enabled = false;

int log_stream_vprintf(const char *format, va_list args) {
  va_list serial_args;
  va_copy(serial_args, args);
  const int result = g_serial_vprintf == nullptr ? std::vprintf(format, serial_args) : g_serial_vprintf(format, serial_args);
  va_end(serial_args);

  if (!g_udp_enabled || g_udp_socket < 0) {
    return result;
  }

  char line[kLogLineMaxBytes];
  va_list network_args;
  va_copy(network_args, args);
  const int written = std::vsnprintf(line, sizeof(line), format, network_args);
  va_end(network_args);

  if (written > 0) {
    const int length = written < static_cast<int>(sizeof(line)) ? written : static_cast<int>(sizeof(line)) - 1;
    sendto(g_udp_socket, line, length, 0, reinterpret_cast<sockaddr *>(&g_udp_destination), sizeof(g_udp_destination));
  }

  return result;
}
}  // namespace

namespace hexe::system {

void init_log_stream() {
  if (!hexe::config::kEndpointLogStreamEnabled || g_udp_enabled) {
    return;
  }

  const in_addr_t host = inet_addr(hexe::config::kEndpointLogStreamHost);
  if (host == INADDR_NONE) {
    ESP_LOGW(kTag, "Wi-Fi log stream host must be an IPv4 address: %s", hexe::config::kEndpointLogStreamHost);
    return;
  }

  g_udp_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
  if (g_udp_socket < 0) {
    ESP_LOGW(kTag, "Failed to create Wi-Fi log stream UDP socket");
    return;
  }

  std::memset(&g_udp_destination, 0, sizeof(g_udp_destination));
  g_udp_destination.sin_family = AF_INET;
  g_udp_destination.sin_addr.s_addr = host;
  g_udp_destination.sin_port = htons(hexe::config::kEndpointLogStreamUdpPort);

  g_udp_enabled = true;
  g_serial_vprintf = esp_log_set_vprintf(log_stream_vprintf);
  ESP_LOGI(
      kTag,
      "Wi-Fi log stream enabled to %s:%d",
      hexe::config::kEndpointLogStreamHost,
      hexe::config::kEndpointLogStreamUdpPort);
}

}  // namespace hexe::system
