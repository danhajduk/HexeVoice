from pathlib import Path


DOC = Path("docs/firmware-production-readiness.md")
FIRMWARE_README = Path("firmware/README.md")
RELEASE_ARTIFACTS_DOC = Path("docs/firmware-release-artifacts.md")


def test_firmware_production_readiness_doc_exists_and_locks_required_topics():
    doc = DOC.read_text(encoding="utf-8")
    normalized_doc = " ".join(doc.split())

    for topic in (
        "Production Gate",
        "Signing Policy",
        "Key Rotation",
        "Secure Boot And Flash Encryption",
        "Provisioning And Manufacturing Flow",
        "Recovery And Field Service",
        "Enclosure And Hardware Readiness",
        "Release Evidence",
    ):
        assert f"## {topic}" in doc

    for required_text in (
        "Flash Encryption release mode",
        "Secure Boot v2",
        "unique flash-encryption key per physical device",
        "Private release keys must never be present",
        "Revoked keys must be rejected",
        "anti-rollback",
        "provisioning.env",
        "Recovery must reject recovery-app updates",
        "Microphone openings",
        "Speaker openings",
        "Hardware mute",
        "Service access",
        "raw credential payloads",
    ):
        assert required_text in normalized_doc


def test_firmware_production_readiness_separates_current_status_from_gate():
    doc = DOC.read_text(encoding="utf-8")

    assert "Current repository status: development artifacts use local signing" in doc
    assert "not completed production enablement" in doc
    assert "Board profiles that have not completed these physical checks" in doc
    assert "must not be marked production-ready" in doc


def test_firmware_docs_link_production_readiness_contract():
    target = "docs/firmware-production-readiness.md"

    assert target in FIRMWARE_README.read_text(encoding="utf-8")
    assert target in RELEASE_ARTIFACTS_DOC.read_text(encoding="utf-8")
