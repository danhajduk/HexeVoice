from pathlib import Path


def test_provider_setup_final_continue_routes_to_capabilities():
    providers_source = Path("frontend/src/features/setup/ProvidersSetupPage.jsx").read_text(encoding="utf-8")
    app_source = Path("frontend/src/App.jsx").read_text(encoding="utf-8")

    assert "export function ProvidersSetupPage({ onContinue })" in providers_source
    assert "Provider setup saved and runtime install queued." in providers_source
    assert "onContinue?.();" in providers_source
    assert '<ProvidersSetupPage onContinue={() => openSetupSection("capabilities")} />' in app_source
