import { StageCard } from "./cards/StageCard";

export { StageCard };

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
