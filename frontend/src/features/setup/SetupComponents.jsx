import { StageCard } from "./cards/StageCard";

export { StageCard };

const SETUP_HEALTH_CHECKS = [
  ["backend", "Backend"],
  ["frontend", "Frontend"],
  ["runtime_dirs", "Runtime dirs"],
  ["docker", "Docker"],
  ["cuda", "CUDA"],
  ["systemd", "systemd"],
  ["supervisor", "Supervisor"],
  ["host_alias", "Host alias"],
  ["firmware", "Firmware"],
  ["stt_model", "STT"],
  ["tts_model", "TTS"],
  ["wake_model", "Wake"],
];

function toneForStatus(status) {
  if (status === "pass") return "success";
  if (status === "fail") return "danger";
  if (status === "warn") return "warning";
  return "neutral";
}

function checkLabel(checks, id) {
  const check = checks.find((item) => item.id === id);
  return check?.status || "unknown";
}

export function SetupSidebar({ flow }) {
  return (
    <aside className="card stack flow-sidebar">
      <div className="section-heading">
        <h2>Setup Flow</h2>
        <span className="pill">{flow.current?.label || "Idle"}</span>
      </div>
      <div className="flow-steps">
        {flow.steps.map((step, index) => {
          const state = step.complete ? "success" : step.current ? "warning" : "neutral";
          return (
            <div key={step.id} className={`flow-step is-${state}`}>
              {step.complete ? <span className="flow-step-check" aria-label="Completed">✓</span> : null}
              <div className="flow-step-index">{index + 1}</div>
              <div className="flow-step-body">
                <strong>{step.label}</strong>
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

export function SetupHealthCard({ readiness }) {
  const checks = readiness?.checks || [];
  const state = !readiness ? "checking" : readiness.ok ? "ready" : "needs attention";
  const tone = !readiness ? "neutral" : readiness.ok ? "success" : "warning";

  return (
    <article className="card stack setup-health-card">
      <div className="section-heading">
        <h2>Setup Health</h2>
        <span className={`status-pill status-pill-${tone}`}>{state}</span>
      </div>

      <div className="setup-health-pills">
        {SETUP_HEALTH_CHECKS.map(([id, label]) => {
          const status = checkLabel(checks, id);
          return (
            <span className={`status-pill status-pill-${toneForStatus(status)}`} key={id}>
              {label}: {status}
            </span>
          );
        })}
      </div>

      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Host</span>
          <span className="fact-grid-value">{readiness?.hostname || "loading"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">LAN</span>
          <span className="fact-grid-value">{readiness?.lan_host || "pending"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Production setup</span>
          <span className="fact-grid-value">{readiness?.production_setup_url || "/setup/host"}</span>
        </div>
      </div>
    </article>
  );
}
