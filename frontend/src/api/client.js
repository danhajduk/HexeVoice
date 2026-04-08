const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:9000";

async function fetchJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`request failed (${response.status})`);
  }
  return response.json();
}

export async function getNodeStatus() {
  return fetchJson("/api/node/status");
}

export async function getOnboardingStatus() {
  return fetchJson("/api/onboarding/status");
}
