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

export async function getSetupBootstrapStatus() {
  return fetchJson("/api/setup/bootstrap/status");
}

export async function getSetupHostReadiness() {
  return fetchJson("/api/setup/host-readiness");
}

export async function runSetupHostReadinessAction(action, payload = {}) {
  return sendJson(`/api/setup/host-readiness/actions/${encodeURIComponent(action)}`, { body: payload });
}

export async function restartOnboardingSetup() {
  return sendJson("/api/onboarding/restart");
}

export async function exportNodeMigrationBundle() {
  return sendJson("/api/node/migration/export", { body: {} });
}

export async function importNodeMigrationBundle(payload) {
  return sendJson("/api/node/migration/import", { body: payload });
}

export async function saveNodeIdentity(payload) {
  return sendJson("/api/onboarding/local-setup/node-identity", { method: "PUT", body: payload });
}

export async function saveCoreConnection(payload) {
  return sendJson("/api/onboarding/local-setup/core-connection", { method: "PUT", body: payload });
}

export async function saveSetupCoreConnection(payload) {
  return sendJson("/api/setup/core", { method: "PUT", body: payload });
}

export async function preflightSetupMigration(payload) {
  return sendJson("/api/setup/migration/preflight", { body: payload });
}

export async function importSetupMigration(payload) {
  return sendJson("/api/setup/migration/import", { body: payload });
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

export async function refreshRegistrationMetadata() {
  return sendJson("/api/onboarding/registration-metadata/refresh");
}

export async function getProviderSetup() {
  return fetchJson("/api/providers/setup");
}

export async function saveProviderSetup(payload) {
  return sendJson("/api/providers/setup", { method: "PUT", body: payload });
}

export async function saveProviderConfig(providerId, payload) {
  return sendJson(`/api/node/ui/providers/${providerId}/setup`, { method: "PUT", body: payload });
}

export async function getCapabilities() {
  return fetchJson("/api/capabilities");
}

export async function saveCapabilitySelection(payload) {
  return sendJson("/api/capabilities/selection", { method: "PUT", body: payload });
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

export async function getServicesStatus() {
  return fetchJson("/api/services/status");
}

export async function restartService(target) {
  return sendJson("/api/services/restart", { body: { target } });
}

export async function getVoiceStatus() {
  return fetchJson("/api/voice/status");
}

export async function getTtsSettings() {
  return fetchJson("/api/tts/settings");
}

export async function saveTtsSettings(payload) {
  return sendJson("/api/tts/settings", { method: "PUT", body: payload });
}

export async function getVoiceSessions({ limit = 12, endpointId } = {}) {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (endpointId) {
    params.set("endpoint_id", endpointId);
  }
  return fetchJson(`/api/voice/sessions?${params.toString()}`);
}

export async function getVoiceSession(sessionId) {
  return fetchJson(`/api/voice/sessions/${encodeURIComponent(sessionId)}`);
}

export async function replayVoiceSession(sessionId, endpointId) {
  return sendJson(`/api/voice/sessions/${encodeURIComponent(sessionId)}/replay`, {
    body: endpointId ? { endpoint_id: endpointId } : {},
  });
}

export function wakeRecordingAudioUrl(recordingId) {
  return `${API_BASE}/api/voice/wake-recordings/${encodeURIComponent(recordingId)}`;
}

export async function deleteWakeRecording(recordingId) {
  return sendJson(`/api/voice/wake-recordings/${encodeURIComponent(recordingId)}`, { method: "DELETE" });
}

export async function deleteVoiceTtsArtifact(streamId) {
  return sendJson(`/api/voice/tts/artifacts/${encodeURIComponent(streamId)}`, { method: "DELETE" });
}

export async function deleteEndpointVoiceArtifacts(endpointId) {
  return sendJson(`/api/voice/artifacts/endpoints/${encodeURIComponent(endpointId)}`, { method: "DELETE" });
}

export async function getVoiceIntents() {
  return fetchJson("/api/voice/intents");
}

export async function dispatchVoiceIntent(payload) {
  return sendJson("/api/voice/intents/dispatch", { body: payload });
}

export async function invokeVoiceIntent(payload) {
  return sendJson("/api/voice/intents/invoke", { body: payload });
}

export async function registerVoiceIntent(payload) {
  return sendJson("/api/voice/intents", { body: payload });
}

export async function updateVoiceIntentLifecycle(intentId, payload) {
  return sendJson(`/api/voice/intents/${encodeURIComponent(intentId)}/lifecycle`, { body: payload });
}

export async function reviewVoiceIntent(intentId, payload) {
  return sendJson(`/api/voice/intents/${encodeURIComponent(intentId)}/review`, { body: payload });
}

export async function getEndpointStatus() {
  return fetchJson("/api/endpoint/status");
}

export async function pushFirmwareOta({ endpointId, filename, version }) {
  return sendJson("/api/firmware/ota/push", {
    body: {
      endpoint_id: endpointId,
      filename,
      version,
    },
  });
}

export async function getEndpointMediaAssets() {
  return fetchJson("/api/endpoint/media");
}

export async function uploadEndpointMedia(payload) {
  return sendJson("/api/endpoint/media", { body: payload });
}

export async function deleteEndpointMedia(assetId) {
  return sendJson(`/api/endpoint/media/${encodeURIComponent(assetId)}`, { method: "DELETE" });
}

export async function deliverEndpointMedia(assetId, endpointId, options = {}) {
  const rewrite = options.rewrite ?? options.overwrite ?? true;
  return sendJson(`/api/endpoint/media/${encodeURIComponent(assetId)}/deliver`, {
    body: {
      endpoint_id: endpointId,
      overwrite: rewrite,
      rewrite,
      activate: options.activate !== false,
    },
  });
}

export async function getEndpointMediaInventory(endpointId) {
  return fetchJson(`/api/endpoint/media/inventory/${encodeURIComponent(endpointId)}`);
}

export async function reformatEndpointStorage(endpointId) {
  return sendJson("/api/endpoint/storage/reformat", {
    body: {
      endpoint_id: endpointId,
    },
  });
}

export async function updateEndpointMetadata(endpointId, metadata) {
  return sendJson(`/api/endpoints/${encodeURIComponent(endpointId)}`, {
    method: "PATCH",
    body: metadata,
  });
}

export async function cancelVoiceSession() {
  return sendJson("/api/voice/session/cancel");
}

export async function getEndpointVolume(endpointId) {
  return fetchJson(`/api/endpoint/volume/${encodeURIComponent(endpointId)}`);
}

export async function setEndpointVolume(endpointId, volumePercent) {
  return sendJson("/api/endpoint/volume", {
    body: {
      endpoint_id: endpointId,
      volume_percent: volumePercent,
    },
  });
}

export async function muteEndpoint(endpointId, muted) {
  return sendJson("/api/endpoint/mute", {
    body: {
      endpoint_id: endpointId,
      muted,
    },
  });
}

export async function cancelEndpointSession(endpointId) {
  return sendJson("/api/endpoint/session/cancel", {
    body: {
      endpoint_id: endpointId,
    },
  });
}

export async function replayEndpointResponse(endpointId) {
  return sendJson("/api/endpoint/replay", {
    body: {
      endpoint_id: endpointId,
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
