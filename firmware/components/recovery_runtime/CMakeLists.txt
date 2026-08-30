set(HEXE_FIRMWARE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../..")
set(HEXE_BOARD_PROFILE_ROOT "${HEXE_FIRMWARE_ROOT}/boards")
set(HEXE_BOARD_PROFILE_GENERATOR "${HEXE_FIRMWARE_ROOT}/tools/generate_board_profile_config.py")
file(GLOB HEXE_BOARD_PROFILE_CONFIGS "${HEXE_BOARD_PROFILE_ROOT}/*/board.yaml")
set_property(
  DIRECTORY
  APPEND
  PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${HEXE_BOARD_PROFILE_GENERATOR}"
    ${HEXE_BOARD_PROFILE_CONFIGS}
)

if(DEFINED ENV{HEXE_BOARD_PROFILE} AND NOT "$ENV{HEXE_BOARD_PROFILE}" STREQUAL "")
  set(HEXE_BOARD_PROFILE "$ENV{HEXE_BOARD_PROFILE}" CACHE STRING "Hexe firmware board profile" FORCE)
elseif(NOT DEFINED HEXE_BOARD_PROFILE OR HEXE_BOARD_PROFILE STREQUAL "")
  set(HEXE_BOARD_PROFILE "esp_box_3")
endif()

set(HEXE_GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
execute_process(
  COMMAND python3
    "${HEXE_BOARD_PROFILE_GENERATOR}"
    --profile-root "${HEXE_BOARD_PROFILE_ROOT}"
    --board-profile "${HEXE_BOARD_PROFILE}"
    --output "${HEXE_GENERATED_DIR}/board_profile_config.cmake"
    --header-output "${HEXE_GENERATED_DIR}/board_profile_pins.h"
  RESULT_VARIABLE HEXE_BOARD_PROFILE_CONFIG_RESULT
)
if(NOT HEXE_BOARD_PROFILE_CONFIG_RESULT EQUAL 0)
  message(FATAL_ERROR "Failed to generate recovery board profile config for ${HEXE_BOARD_PROFILE}")
endif()
include("${HEXE_GENERATED_DIR}/board_profile_config.cmake")

if(NOT HEXE_BOARD_RECOVERY_APP)
  message(FATAL_ERROR "Board profile ${HEXE_BOARD_PROFILE} does not support a recovery app")
endif()
if(NOT HEXE_BOARD_IDF_TARGET STREQUAL "esp32s3")
  message(FATAL_ERROR "Recovery skeleton currently supports only S3 targets, got ${HEXE_BOARD_IDF_TARGET}")
endif()

message(STATUS "Hexe recovery board profile: ${HEXE_BOARD_PROFILE}")
message(STATUS "Hexe recovery partition schema: ${HEXE_BOARD_PARTITION_SCHEMA}")

idf_component_register(
  SRCS
    "recovery_status.cpp"
  INCLUDE_DIRS
    "."
    "${HEXE_GENERATED_DIR}"
  REQUIRES
    app_update
    esp_app_format
    esp_partition
    esp_psram
    esp_system
    freertos
    heap
    log
    nvs_flash
    spi_flash
)

target_compile_definitions(
  ${COMPONENT_LIB}
  PRIVATE
    ${HEXE_BOARD_DEFINITIONS}
    HEXE_FIRMWARE_APPLICATION_TYPE_RECOVERY=1
)
