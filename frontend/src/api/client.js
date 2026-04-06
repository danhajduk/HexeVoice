const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:9000";

export async function getNodeStatus() {
  const response = await fetch(`${API_BASE}/api/node/status`);
  if (!response.ok) {
    throw new Error(`request failed (${response.status})`);
  }
  return response.json();
}
