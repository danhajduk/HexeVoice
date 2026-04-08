import { useCallback, useEffect, useState } from "react";
import {
  getCapabilities,
  getGovernanceCurrent,
  getNodeStatus,
  getOnboardingStatus,
  getOperationalStatus,
  getProviderSetup,
} from "./api/client";
import { StatusCard } from "./components/status/StatusCard";
import { OnboardingPanel } from "./features/onboarding/OnboardingPanel";
import { OperationalPanel } from "./features/operational/OperationalPanel";
import { ProviderPanel } from "./features/providers/ProviderPanel";
import { DiagnosticsPanel } from "./features/diagnostics/DiagnosticsPanel";

function statusTone(status) {
  if (!status) {
    return "neutral";
  }
  if (status.operational_ready) {
    return "success";
  }
  if (status.trust_state === "revoked") {
    return "danger";
  }
  if (status.current_step_id === "approval" || status.current_step_id === "governance_sync") {
    return "warning";
  }
  return "neutral";
}

export default function App() {
  const [status, setStatus] = useState(null);
  const [onboarding, setOnboarding] = useState(null);
  const [providerSetup, setProviderSetup] = useState(null);
  const [capabilities, setCapabilities] = useState(null);
  const [governance, setGovernance] = useState(null);
  const [operational, setOperational] = useState(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const [
      statusPayload,
      onboardingPayload,
      providerPayload,
      capabilityPayload,
      governancePayload,
      operationalPayload,
    ] = await Promise.all([
      getNodeStatus(),
      getOnboardingStatus(),
      getProviderSetup().catch(() => null),
      getCapabilities().catch(() => null),
      getGovernanceCurrent().catch(() => null),
      getOperationalStatus().catch(() => null),
    ]);
    setStatus(statusPayload);
    setOnboarding(onboardingPayload);
    setProviderSetup(providerPayload);
    setCapabilities(capabilityPayload);
    setGovernance(governancePayload);
    setOperational(operationalPayload);
    setError("");
  }, []);

  useEffect(() => {
    let mounted = true;
    Promise.all([
      getNodeStatus(),
      getOnboardingStatus(),
      getProviderSetup().catch(() => null),
      getCapabilities().catch(() => null),
      getGovernanceCurrent().catch(() => null),
      getOperationalStatus().catch(() => null),
    ])
      .then(([statusPayload, onboardingPayload, providerPayload, capabilityPayload, governancePayload, operationalPayload]) => {
        if (!mounted) {
          return;
        }
        setStatus(statusPayload);
        setOnboarding(onboardingPayload);
        setProviderSetup(providerPayload);
        setCapabilities(capabilityPayload);
        setGovernance(governancePayload);
        setOperational(operationalPayload);
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
    <main className="app-page">
      <div className="app-shell">
        <section className="app-hero">
          <div className="hero-card">
            <div className="eyebrow-row">
              <span className="eyebrow">Hexe Node Console</span>
              <span className={`status-pill status-pill-${statusTone(status)}`}>
                {onboarding?.current_step_label || status?.current_step_label || "Loading status"}
              </span>
            </div>
            <h1 className="app-title">HexeVoice</h1>
            <p className="hero-copy">
              Voice-node onboarding, trust activation, provider setup, and operational readiness in the shared
              Hexe operator shell.
            </p>
            <div className="hero-facts">
              <div className="fact-card">
                <span className="fact-label">Lifecycle</span>
                <span className="fact-value">{onboarding?.lifecycle_state || status?.lifecycle_state || "loading"}</span>
              </div>
              <div className="fact-card">
                <span className="fact-label">Trust</span>
                <span className="fact-value">{status?.trust_state || "loading"}</span>
              </div>
              <div className="fact-card">
                <span className="fact-label">Readiness</span>
                <span className="fact-value">{status?.operational_ready ? "operational" : "blocked"}</span>
              </div>
            </div>
          </div>
          <StatusCard status={status} onboarding={onboarding} error={error} />
        </section>

        <section className="shell-grid">
          <aside className="shell-rail">
            <div className="rail-card">
              <h2 className="rail-title">Setup Flow</h2>
              <p className="rail-copy">
                The canonical 10-step Core lifecycle drives this node from first launch to trusted, provider-ready
                operation.
              </p>
              <div className="step-list">
                {(onboarding?.steps || []).map((step, index) => (
                  <div
                    key={step.step_id}
                    className={`step-item ${step.current ? "step-item-current" : ""} ${step.complete ? "step-item-complete" : ""}`}
                  >
                    <span className="step-index">{index + 1}</span>
                    <div>
                      <span className="step-label">{step.label}</span>
                      <span className="step-meta">{step.lifecycle_state}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </aside>

          <div className="main-grid">
            <OnboardingPanel status={status} onboarding={onboarding} onRefresh={refresh} />
            <OperationalPanel status={status} operational={operational} governance={governance} />
            <ProviderPanel status={status} providerSetup={providerSetup} capabilities={capabilities} />
            <DiagnosticsPanel status={status} onboarding={onboarding} operational={operational} onRefresh={refresh} />
          </div>
        </section>
      </div>
    </main>
  );
}
