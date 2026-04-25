#pragma once

namespace hexe {

enum class AppPhase {
  kBooting,
  kWiFiConnecting,
  kBackendConnecting,
  kIdle,
  kListening,
  kThinking,
  kReplying,
  kUpdating,
  kMuted,
  kTimerFinished,
  kError,
};

struct AppState {
  AppPhase phase{AppPhase::kBooting};
  bool muted{false};
  bool wifi_connected{false};
  bool backend_connected{false};
  bool timer_active{false};
  bool vad_enabled{false};
  bool vad_speaking{false};
  bool audio_streaming{false};
  int wifi_rssi{-100};
  int vad_level{0};
  int loading_frame{0};
};

AppState &state();
void advance_loading_frame();

}  // namespace hexe
