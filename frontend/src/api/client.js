const API_BASE = import.meta.env.VITE_API_BASE || "";

function formatApiDetail(detail, fallback) {
  if (!detail) {
    return fallback;
  }

  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        if (item && typeof item === "object") {
          const location = Array.isArray(item.loc) ? item.loc.join(".") : null;
          const message = typeof item.msg === "string" ? item.msg : JSON.stringify(item);
          return location ? `${location}: ${message}` : message;
        }
        return String(item);
      })
      .filter(Boolean);

    return messages.join("; ") || fallback;
  }

  if (typeof detail === "object") {
    if (typeof detail.message === "string") {
      return detail.message;
    }
    return JSON.stringify(detail);
  }

  return String(detail);
}

async function fetchJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    let detail = `request failed (${response.status})`;
    try {
      const payload = await response.json();
      detail = formatApiDetail(payload?.detail, detail);
    } catch {
      // Ignore non-JSON error bodies.
    }
    throw new Error(detail);
  }
  return response.json();
}

async function sendJson(path, { method = "POST", body } = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let detail = `request failed (${response.status})`;
    try {
      const payload = await response.json();
      detail = formatApiDetail(payload?.detail, detail);
    } catch {
      // Ignore non-JSON error bodies.
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function getNodeStatus() {
  return fetchJson("/api/node/status");
}

export async function getOnboardingStatus() {
  return fetchJson("/api/onboarding/status");
}

export async function getLocalSetup() {
  return fetchJson("/api/onboarding/local-setup");
}

export async function restartOnboardingSetup() {
  return sendJson("/api/onboarding/restart");
}

export async function saveNodeIdentity(payload) {
  return sendJson("/api/onboarding/local-setup/node-identity", { method: "PUT", body: payload });
}

export async function saveCoreConnection(payload) {
  return sendJson("/api/onboarding/local-setup/core-connection", { method: "PUT", body: payload });
}

export async function getBootstrapDiscovery() {
  return fetchJson("/api/onboarding/bootstrap-discovery");
}

export async function testBootstrapConnection() {
  return sendJson("/api/onboarding/bootstrap-discovery/test-connection");
}

export async function validateBootstrapAdvertisement(payload) {
  return sendJson("/api/onboarding/bootstrap-discovery/advertisement", { method: "PUT", body: payload });
}

export async function startOnboardingSession() {
  return sendJson("/api/onboarding/session/start");
}

export async function pollOnboardingSession() {
  return sendJson("/api/onboarding/session/poll");
}

export async function finalizeTrustActivation() {
  return sendJson("/api/onboarding/trust-activation/finalize");
}

export async function getProviderSetup() {
  return fetchJson("/api/providers/setup");
}

export async function saveProviderSetup(payload) {
  return sendJson("/api/providers/setup", { method: "PUT", body: payload });
}

export async function getCapabilities() {
  return fetchJson("/api/capabilities");
}

export async function declareCapabilities() {
  return sendJson("/api/capabilities/declaration");
}

export async function getGovernanceCurrent() {
  return fetchJson("/api/governance/current");
}

export async function refreshGovernance() {
  return sendJson("/api/governance/refresh");
}

export async function getOperationalStatus() {
  return fetchJson("/api/node/operational-status");
}

export async function getVoiceStatus() {
  return fetchJson("/api/voice/status");
}

export async function getEndpointStatus() {
  return fetchJson("/api/endpoint/status");
}

export async function cancelVoiceSession() {
  return sendJson("/api/voice/session/cancel");
}

export async function setEndpointVolume(endpointId, volumePercent) {
  return sendJson("/api/endpoint/volume", {
    body: {
      endpoint_id: endpointId,
      volume_percent: volumePercent,
    },
  });
}

export async function testAssistantTurn(endpointId = "dashboard-test") {
  return sendJson("/api/assistant/turn", {
    body: {
      endpoint_id: endpointId,
      text: "hello",
    },
  });
}
