from pathlib import Path


RECOVERY_DOC = Path("docs/firmware-recovery-architecture.md")
ROOT_CMAKE = Path("firmware/CMakeLists.txt")
BUILD_SCRIPT = Path("firmware/build.sh")
RECOVERY_README = Path("firmware/apps/recovery/README.md")
RECOVERY_MAIN_CMAKE = Path("firmware/apps/recovery/main/CMakeLists.txt")
RECOVERY_MAIN = Path("firmware/apps/recovery/main/app_main.cpp")
RECOVERY_RUNTIME_CMAKE = Path("firmware/components/recovery_runtime/CMakeLists.txt")
RECOVERY_STATUS = Path("firmware/components/recovery_runtime/recovery_status.cpp")
RECOVERY_STATUS_HEADER = Path("firmware/components/recovery_runtime/recovery_status.h")
PARTITION_ROADMAP = Path("docs/firmware-partition-ota-roadmap.md")


def test_recovery_architecture_defines_app_boundary_and_build_lane():
    doc = RECOVERY_DOC.read_text()
    root_cmake = ROOT_CMAKE.read_text()

    assert "Status: Task 273 architecture contract; Task 274 skeleton implemented" in doc
    assert "HEXE_FIRMWARE_APP=recovery" in doc
    assert "firmware/apps/recovery/main/" in doc
    assert "application_type=recovery" in doc
    assert "Recovery must not link `endpoint_runtime`." in doc
    assert "HEXE_FIRMWARE_APP" in root_cmake
    assert "HEXE_FIRMWARE_RUNTIME_COMPONENT endpoint_runtime" in root_cmake
    assert "HEXE_FIRMWARE_RUNTIME_COMPONENT recovery_runtime" in root_cmake
    assert 'apps/${HEXE_FIRMWARE_APP}/main' in root_cmake
    assert "HEXE_FIRMWARE_RUNTIME_DIR" in root_cmake
    assert "COMPONENT_DIRS" in root_cmake
    assert '$ENV{IDF_PATH}/components' in root_cmake
    assert "set(COMPONENTS main ${HEXE_FIRMWARE_RUNTIME_COMPONENT})" in root_cmake


def test_recovery_architecture_defines_entry_conditions_and_interfaces():
    doc = RECOVERY_DOC.read_text()

    for entry_reason in (
        "physical_gesture",
        "main_requested",
        "no_valid_main_slot",
        "boot_failure_threshold",
        "factory_or_serial",
    ):
        assert entry_reason in doc

    assert "Recovery must work without Hexe Core and without an SD card." in doc
    assert "temporary Wi-Fi AP" in doc
    assert "serial console diagnostics" in doc
    assert "minimal display/touch status" in doc


def test_recovery_architecture_locks_recovery_diagnostics_api():
    doc = RECOVERY_DOC.read_text()

    for path in (
        "/api/recovery/status",
        "/api/recovery/partitions",
        "/api/recovery/diagnostics",
        "/api/recovery/wifi",
        "/api/recovery/endpoint",
        "/api/recovery/firmware/install",
        "/api/recovery/boot/select",
        "/api/recovery/config/reset",
    ):
        assert path in doc

    assert '"schema_version": "hexe-recovery-status-v1"' in doc
    assert '"recovery_api_version": "hexe-recovery-api-v1"' in doc
    assert '"core_required": false' in doc
    assert '"sd_required": false' in doc


def test_recovery_architecture_links_from_existing_firmware_docs():
    recovery_readme = RECOVERY_README.read_text()
    partition_roadmap = PARTITION_ROADMAP.read_text()

    assert "docs/firmware-recovery-architecture.md" in recovery_readme
    assert "docs/firmware-recovery-architecture.md" in partition_roadmap


def test_recovery_skeleton_has_bootable_app_and_runtime_component():
    main_cmake = RECOVERY_MAIN_CMAKE.read_text()
    main_source = RECOVERY_MAIN.read_text()
    runtime_cmake = RECOVERY_RUNTIME_CMAKE.read_text()
    status_header = RECOVERY_STATUS_HEADER.read_text()

    assert '"app_main.cpp"' in main_cmake
    assert "recovery_runtime" in main_cmake
    assert "endpoint_runtime" not in main_cmake
    assert 'extern "C" void app_main(void)' in main_source
    assert "init_recovery_runtime();" in main_source
    assert "log_recovery_status();" in main_source
    assert '"recovery_status.cpp"' in runtime_cmake
    assert "generate_board_profile_config.py" in runtime_cmake
    assert "board_profile_pins.h" in runtime_cmake
    assert "HEXE_BOARD_RECOVERY_APP" in runtime_cmake
    assert 'HEXE_BOARD_IDF_TARGET STREQUAL "esp32s3"' in runtime_cmake
    assert "endpoint_runtime" not in runtime_cmake
    assert "esp-tflite-micro" not in runtime_cmake
    assert "render_status_json()" in status_header


def test_recovery_status_skeleton_reports_safe_serial_json():
    status_source = RECOVERY_STATUS.read_text()

    assert 'kSchemaVersion[] = "hexe-recovery-status-v1"' in status_source
    assert 'kRecoveryApiVersion[] = "hexe-recovery-api-v1"' in status_source
    assert 'kApplicationType[] = "recovery"' in status_source
    assert '\\"core_required\\":false' in status_source
    assert '\\"sd_required\\":false' in status_source
    assert '\\"models_required\\":false' in status_source
    assert '\\"endpoint_runtime_linked\\":false' in status_source
    assert '\\"network\\":{\\"mode\\":\\"not_started\\",\\"temporary_ap_active\\":false}' in status_source
    assert '\\"interfaces\\":{\\"serial_console\\":true,\\"http_api\\":false' in status_source
    assert "esp_ota_get_running_partition" in status_source
    assert "esp_flash_get_size" in status_source
    assert "esp_psram_is_initialized" in status_source
    assert "nvs_flash_init" in status_source


def test_build_script_exports_recovery_metadata_without_endpoint_runtime():
    build_script = BUILD_SCRIPT.read_text()

    assert "runtime_component_for_app()" in build_script
    assert 'endpoint) echo "endpoint_runtime"' in build_script
    assert 'recovery) echo "recovery_runtime"' in build_script
    assert 'FIRMWARE_APPLICATION_TYPE="${FIRMWARE_APP}"' in build_script
    assert 'FIRMWARE_API_VERSION="$(firmware_api_version_for_app "${FIRMWARE_APP}")"' in build_script
    assert 'GENERATED_COMPONENT_NAME="$(runtime_component_for_app "${FIRMWARE_APP}")"' in build_script
    assert 'idf_env+=("IDF_COMPONENT_MANAGER=0")' in build_script
    assert 'push mode supports only HEXE_FIRMWARE_APP=endpoint.' in build_script
    assert 'hexe_${FIRMWARE_APP}_${1}.bin' in build_script
