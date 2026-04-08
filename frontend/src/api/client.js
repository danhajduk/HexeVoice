const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:9000";

async function fetchJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`request failed (${response.status})`);
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
      detail = payload?.detail || detail;
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
