set(HEXE_ENDPOINT_CONFIG "${CMAKE_CURRENT_LIST_DIR}/../config/endpoint.yaml")
if(NOT EXISTS "${HEXE_ENDPOINT_CONFIG}")
  set(HEXE_ENDPOINT_CONFIG "${CMAKE_CURRENT_LIST_DIR}/../config/endpoint.example.yaml")
endif()

set(HEXE_GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
execute_process(
  COMMAND python3
    "${CMAKE_CURRENT_LIST_DIR}/../tools/generate_endpoint_config.py"
    --input "${HEXE_ENDPOINT_CONFIG}"
    --output "${HEXE_GENERATED_DIR}/endpoint_config.h"
  RESULT_VARIABLE HEXE_ENDPOINT_CONFIG_RESULT
)
if(NOT HEXE_ENDPOINT_CONFIG_RESULT EQUAL 0)
  message(FATAL_ERROR "Failed to generate endpoint_config.h from ${HEXE_ENDPOINT_CONFIG}")
endif()

idf_component_register(
  SRCS
    "app_main.cpp"
    "app_state.cpp"
    "board/audio.cpp"
    "board/buttons.cpp"
    "board/display.cpp"
    "board/storage.cpp"
    "board/wifi.cpp"
    "system/ota.cpp"
    "system/power.cpp"
    "system/settings.cpp"
    "system/telemetry.cpp"
    "ui/animator.cpp"
    "ui/screens.cpp"
    "voice/assistant_client.cpp"
    "voice/backend_client.cpp"
    "voice/stt_stream.cpp"
    "voice/tts_player.cpp"
    "voice/wake_word.cpp"
  INCLUDE_DIRS
    "."
    "${HEXE_GENERATED_DIR}"
  REQUIRES
    app_update
    esp_app_format
    esp_event
    esp_https_ota
    esp_http_client
    esp_lcd
    esp_netif
    esp_websocket_client
    esp_wifi
    cjson
    mbedtls
    nvs_flash
)
