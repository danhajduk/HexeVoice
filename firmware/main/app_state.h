#pragma once

#include <cstdint>

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

enum class PlaybackLifecycleState {
  kIdle,
  kQueued,
  kStarted,
  kFinished,
  kFailed,
  kStopped,
};

enum class TimerLifecycleState {
  kInactive,
  kActive,
  kPaused,
  kFinished,
};

struct AppState {
  AppPhase phase{AppPhase::kBooting};
  bool muted{false};
  bool wifi_connected{false};
  bool backend_connected{false};
  bool voice_ws_connected{false};
  bool timer_active{false};
  TimerLifecycleState timer_state{TimerLifecycleState::kInactive};
  int64_t timer_due_unix_ms{0};
  int64_t timer_remaining_ms{0};
  int timer_duration_seconds{0};
  char timer_label[48]{};
  bool vad_enabled{false};
  bool vad_speaking{false};
  bool audio_streaming{false};
  bool tts_playback_active{false};
  bool mic_paused_for_playback{false};
  bool media_transfer_active{false};
  PlaybackLifecycleState tts_playback_state{PlaybackLifecycleState::kIdle};
  int wifi_rssi{-100};
  int vad_level{0};
  int loading_frame{0};
  int output_volume_percent{70};
  int micro_vad_pause_ms{190};
  bool ota_active{false};
  int ota_progress_percent{0};
  int ota_bytes_read{0};
  int ota_size_bytes{0};
};

AppState &state();
void advance_loading_frame();
bool endpoint_ready();
AppPhase idle_or_connecting_phase();

}  // namespace hexe
