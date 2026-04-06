import { useEffect, useState } from "react";
import { getNodeStatus } from "./api/client";
import { StatusCard } from "./components/status/StatusCard";
import { OnboardingPanel } from "./features/onboarding/OnboardingPanel";
import { OperationalPanel } from "./features/operational/OperationalPanel";
import { ProviderPanel } from "./features/providers/ProviderPanel";
import { DiagnosticsPanel } from "./features/diagnostics/DiagnosticsPanel";

export default function App() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;
    getNodeStatus()
      .then((payload) => {
        if (mounted) {
          setStatus(payload);
        }
      })
      .catch((err) => {
        if (mounted) {
          setError(String(err.message || err));
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <main className="app-shell">
      <p className="eyebrow">Hexe Node</p>
      <h1>HexeVoice</h1>
      <p className="muted">
        Starter operator surface for onboarding, readiness, provider wiring, and diagnostics.
      </p>
      <StatusCard status={status} error={error} />
      <section className="panel-grid">
        <OnboardingPanel status={status} />
        <OperationalPanel status={status} />
        <ProviderPanel />
        <DiagnosticsPanel status={status} />
      </section>
    </main>
  );
}
