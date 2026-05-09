set(HEXE_ENDPOINT_CONFIG "${CMAKE_CURRENT_LIST_DIR}/../config/endpoint.yaml")
if(NOT EXISTS "${HEXE_ENDPOINT_CONFIG}")
  set(HEXE_ENDPOINT_CONFIG "${CMAKE_CURRENT_LIST_DIR}/../config/endpoint.example.yaml")
endif()
set(HEXE_ENDPOINT_CONFIG_GENERATOR "${CMAKE_CURRENT_LIST_DIR}/../tools/generate_endpoint_config.py")
set_property(
  DIRECTORY
  APPEND
  PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${HEXE_ENDPOINT_CONFIG}"
    "${HEXE_ENDPOINT_CONFIG_GENERATOR}"
)

set(HEXE_GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
execute_process(
  COMMAND python3
    "${HEXE_ENDPOINT_CONFIG_GENERATOR}"
    --input "${HEXE_ENDPOINT_CONFIG}"
    --output "${HEXE_GENERATED_DIR}/endpoint_config.h"
  RESULT_VARIABLE HEXE_ENDPOINT_CONFIG_RESULT
)
if(NOT HEXE_ENDPOINT_CONFIG_RESULT EQUAL 0)
  message(FATAL_ERROR "Failed to generate endpoint_config.h from ${HEXE_ENDPOINT_CONFIG}")
endif()

if(DEFINED ENV{HEXE_BOARD_PROFILE} AND NOT "$ENV{HEXE_BOARD_PROFILE}" STREQUAL "")
  set(HEXE_BOARD_PROFILE "$ENV{HEXE_BOARD_PROFILE}" CACHE STRING "Hexe firmware board profile" FORCE)
elseif(NOT DEFINED HEXE_BOARD_PROFILE OR HEXE_BOARD_PROFILE STREQUAL "")
  set(HEXE_BOARD_PROFILE "esp_box_3")
endif()

set(HEXE_BOARD_SRCS
  "board/audio.cpp"
  "board/buttons.cpp"
  "board/display.cpp"
  "board/storage.cpp"
  "board/touch.cpp"
  "voice/tts_player.cpp"
)

if(HEXE_BOARD_PROFILE STREQUAL "ha_voice_pe")
  set(HEXE_BOARD_SRCS
    "board/audio_ha_voice_pe.cpp"
    "board/buttons_ha_voice_pe.cpp"
    "board/display_none.cpp"
    "board/storage_nvs_only.cpp"
    "board/touch_none.cpp"
    "voice/tts_player_noop.cpp"
  )
  set(HEXE_BOARD_DEFINITIONS HEXE_BOARD_PROFILE_HA_VOICE_PE=1)
elseif(HEXE_BOARD_PROFILE STREQUAL "esp_box_3")
  set(HEXE_BOARD_DEFINITIONS HEXE_BOARD_PROFILE_ESP_BOX_3=1)
else()
  message(FATAL_ERROR "Unsupported HEXE_BOARD_PROFILE: ${HEXE_BOARD_PROFILE}")
endif()

message(STATUS "Hexe board profile: ${HEXE_BOARD_PROFILE}")

idf_component_register(
  SRCS
    "app_main.cpp"
    "app_state.cpp"
    ${HEXE_BOARD_SRCS}
    "board/wifi.cpp"
    "system/clock.cpp"
    "system/ota.cpp"
    "system/log_stream.cpp"
    "system/power.cpp"
    "system/settings.cpp"
    "system/telemetry.cpp"
    "ui/animator.cpp"
    "ui/screens.cpp"
    "voice/assistant_client.cpp"
    "voice/backend_client.cpp"
    "voice/stt_stream.cpp"
    "voice/wake_word.cpp"
  INCLUDE_DIRS
    "."
    "${HEXE_GENERATED_DIR}"
  REQUIRES
    app_update
    esp_app_format
    esp_event
    esp_driver_gpio
    esp_driver_i2c
    esp_driver_i2s
    esp_https_ota
    esp_http_client
    esp_lcd
    esp_netif
    esp_websocket_client
    esp_wifi
    espressif__esp_lcd_touch
    fatfs
    cjson
    lwip
    mbedtls
    nvs_flash
    sdmmc
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE ${HEXE_BOARD_DEFINITIONS})
