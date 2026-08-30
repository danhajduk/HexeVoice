from pathlib import Path


RECOVERY_DOC = Path("docs/firmware-recovery-architecture.md")
ROOT_CMAKE = Path("firmware/CMakeLists.txt")
RECOVERY_README = Path("firmware/apps/recovery/README.md")
PARTITION_ROADMAP = Path("docs/firmware-partition-ota-roadmap.md")


def test_recovery_architecture_defines_app_boundary_and_build_lane():
    doc = RECOVERY_DOC.read_text()
    root_cmake = ROOT_CMAKE.read_text()

    assert "Status: Task 273 architecture contract" in doc
    assert "HEXE_FIRMWARE_APP=recovery" in doc
    assert "firmware/apps/recovery/main/" in doc
    assert "application_type=recovery" in doc
    assert "Recovery must not link `endpoint_runtime`." in doc
    assert "HEXE_FIRMWARE_APP" in root_cmake
    assert 'apps/${HEXE_FIRMWARE_APP}/main' in root_cmake


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

