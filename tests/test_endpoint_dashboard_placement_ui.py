from __future__ import annotations

from pathlib import Path


FRONTEND_ENDPOINT_DASHBOARD = Path("frontend/src/features/dashboard/VoiceEndpointDashboardSection.jsx")


def test_passive_placement_calibration_uses_endpoint_room_fallback() -> None:
    source = FRONTEND_ENDPOINT_DASHBOARD.read_text(encoding="utf-8")

    assert "function endpointPlacementRoom(endpointStatus)" in source
    assert "placementRoom.trim() || endpointPlacementRoom(selectedEndpointStatus)" in source
    assert "setPlacementRoom(calibrationRoom);" in source
    assert "room: calibrationRoom" in source
