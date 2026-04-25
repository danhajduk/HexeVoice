from pathlib import Path
import subprocess
import sys


def test_endpoint_config_generator_uses_yaml_contract(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    output = tmp_path / "endpoint_config.h"

    subprocess.run(
        [
            sys.executable,
            str(repo_root / "firmware/tools/generate_endpoint_config.py"),
            "--input",
            str(repo_root / "firmware/config/endpoint.example.yaml"),
            "--output",
            str(output),
        ],
        check=True,
    )

    header = output.read_text(encoding="utf-8")
    assert 'constexpr const char *kEndpointId = "esp-box-1";' in header
    assert 'constexpr const char *kEndpointBackendHost = "10.0.0.22";' in header
    assert 'constexpr const char *kEndpointHeartbeatPath = "/api/endpoint/heartbeat";' in header
    assert 'constexpr const char *kEndpointVoiceWsPath = "/api/voice/ws";' in header
    assert "constexpr int kEndpointAudioSampleRateHz = 16000;" in header
    assert "constexpr int kEndpointAudioChunkSamples = 320;" in header
    assert "constexpr bool kEndpointLogStreamEnabled = false;" in header
    assert 'constexpr const char *kEndpointLogStreamHost = "10.0.0.22";' in header
    assert "constexpr int kEndpointLogStreamUdpPort = 9010;" in header
