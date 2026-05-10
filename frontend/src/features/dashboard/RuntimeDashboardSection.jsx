import { useMemo, useState } from "react";
import { restartService } from "../../api/client";

function text(value, fallback = "unknown") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function bytes(value) {
  if (!Number.isFinite(value)) {
    return "unknown";
  }
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function percent(value) {
  return Number.isFinite(value) ? `${value.toFixed(1)}%` : "unknown";
}

function statusTone(component) {
  if (!component) {
    return "neutral";
  }
  if (component.healthy === false || ["failed", "error", "not_created", "exited"].includes(component.status)) {
    return "danger";
  }
  if (component.restart_supported === false && component.component_id !== "backend") {
    return "warning";
  }
  return "success";
}

function componentCpu(component) {
  const usage = component?.resource_usage || {};
  return percent(usage.cpu_percent ?? usage.process_cpu_percent);
}

function componentMemory(component) {
  const usage = component?.resource_usage || {};
  if (usage.memory_usage) {
    return usage.memory_percent === null || usage.memory_percent === undefined
      ? usage.memory_usage
      : `${usage.memory_usage} (${percent(usage.memory_percent)})`;
  }
  return `${bytes(usage.process_memory_rss_bytes)} (${percent(usage.process_memory_percent)})`;
}

function componentFromPipeline(componentId, servicesStatus, voiceStatus) {
  const serviceComponents = Array.isArray(servicesStatus?.components) ? servicesStatus.components : [];
  const fallbackLabels = {
    backend: "Backend",
    stt: "STT Engine",
    tts: "TTS Engine",
  };
  const component = serviceComponents.find((item) => item.component_id === componentId) || {
    component_id: componentId,
    label: fallbackLabels[componentId] || componentId,
    status: "pending",
    healthy: false,
    restart_supported: false,
  };
  const pipeline = voiceStatus?.turn_pipeline || {};
  const providerStatus = componentId === "stt" ? pipeline.stt : componentId === "tts" ? pipeline.tts : null;
  if (!providerStatus) {
    return component;
  }
  return {
    ...component,
    status: providerStatus.status || component.status,
    healthy: providerStatus.healthy ?? component.healthy,
    provider: providerStatus.provider || component.provider,
    model: providerStatus.model || component.model,
    last_error: providerStatus.last_error || providerStatus.error || component.last_error,
  };
}

function RuntimeComponentCard({ component, onRestart, actionBusy }) {
  const tone = statusTone(component);
  const canRestart = Boolean(component?.restart_supported && component?.restart_target && !actionBusy);

  return (
    <section className="panel stack runtime-component-card">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Runtime</p>
          <h2 className="panel-title">{text(component?.label)}</h2>
        </div>
        <span className={`status-pill status-pill-${tone}`}>{text(component?.status, "pending")}</span>
      </div>
      <div className="fact-grid">
        <div className="fact-grid-item">
          <span className="fact-grid-label">Provider</span>
          <span className="fact-grid-value">{text(component?.provider)}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Model</span>
          <span className="fact-grid-value">{text(component?.model)}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Restart Target</span>
          <span className="fact-grid-value">{text(component?.restart_target, "unsupported")}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Health</span>
          <span className="fact-grid-value">{component?.healthy === false ? "degraded" : "healthy"}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">CPU</span>
          <span className="fact-grid-value">{componentCpu(component)}</span>
        </div>
        <div className="fact-grid-item">
          <span className="fact-grid-label">Memory</span>
          <span className="fact-grid-value">{componentMemory(component)}</span>
        </div>
      </div>
      {component?.last_error ? <div className="callout callout-danger">{component.last_error}</div> : null}
      {component?.restart_detail ? <div className="callout callout-neutral">{component.restart_detail}</div> : null}
      <div className="actions">
        <button
          className="btn btn-secondary"
          type="button"
          disabled={!canRestart}
          onClick={() => onRestart(component.restart_target)}
        >
          {actionBusy ? "Restarting..." : "Restart"}
        </button>
      </div>
    </section>
  );
}

export function RuntimeDashboardSection({ servicesStatus, voiceStatus, onRefresh }) {
  const [busyTarget, setBusyTarget] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const components = useMemo(
    () => [
      componentFromPipeline("backend", servicesStatus, voiceStatus),
      componentFromPipeline("stt", servicesStatus, voiceStatus),
      componentFromPipeline("tts", servicesStatus, voiceStatus),
    ],
    [servicesStatus, voiceStatus],
  );
  const supervisor = servicesStatus?.supervisor || {};

  async function handleRestart(target) {
    setBusyTarget(target);
    setMessage("");
    setError("");
    try {
      const result = await restartService(target);
      if (!result.accepted) {
        throw new Error(result.detail || result.status || "restart unsupported");
      }
      setMessage(`${target} restart accepted: ${result.status}`);
      await onRefresh?.();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusyTarget("");
    }
  }

  return (
    <section className="grid operational-dashboard-grid">
      <section className="panel stack operational-content-header">
        <div className="section-heading">
          <div>
            <p className="panel-kicker">Supervisor</p>
            <h2 className="panel-title">Runtime Control</h2>
          </div>
          <span className={`status-pill status-pill-${supervisor.configured ? "success" : "warning"}`}>
            {supervisor.configured ? "configured" : "not configured"}
          </span>
        </div>
        <div className="fact-grid">
          <div className="fact-grid-item">
            <span className="fact-grid-label">Registered</span>
            <span className="fact-grid-value">{supervisor.registered ? "yes" : "no"}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Last Seen</span>
            <span className="fact-grid-value">{text(supervisor.last_seen_at, "pending")}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Wake Runtime</span>
            <span className="fact-grid-value">{text(servicesStatus?.openwakeword)}</span>
          </div>
          <div className="fact-grid-item">
            <span className="fact-grid-label">Piper Runtime</span>
            <span className="fact-grid-value">{text(servicesStatus?.piper_tts)}</span>
          </div>
        </div>
        {supervisor.last_error ? <div className="callout callout-danger">{supervisor.last_error}</div> : null}
        {message ? <div className="callout callout-success">{message}</div> : null}
        {error ? <div className="callout callout-danger">{error}</div> : null}
      </section>

      {components.map((component) => (
        <RuntimeComponentCard
          key={component.component_id}
          component={component}
          actionBusy={busyTarget === component.restart_target}
          onRestart={handleRestart}
        />
      ))}
    </section>
  );
}
