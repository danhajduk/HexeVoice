import { useCallback, useEffect, useState } from "react";
import {
  getCapabilities,
  getEndpointStatus,
  getGovernanceCurrent,
  getNodeStatus,
  getOnboardingStatus,
  getOperationalStatus,
  getProviderSetup,
  getServicesStatus,
  getTtsSettings,
  getVoiceIntents,
  getVoiceStatus,
  restartOnboardingSetup,
} from "./api/client";
import { OnboardingPanel } from "./features/onboarding/OnboardingPanel";
import { SetupSidebar } from "./features/setup/SetupComponents";
import { HostSetupPage } from "./features/setup/HostSetupPage";
import { CoreSetupPage, MigrationSetupPage } from "./features/setup/CoreMigrationSetupPage";
import { SetupHeroCard } from "./features/setup/cards/SetupHeroCard";
import { LiveStatusCard } from "./features/setup/cards/LiveStatusCard";
import { OperatorPromptsCard } from "./features/setup/cards/OperatorPromptsCard";
import { DashboardSidebarCard } from "./features/dashboard/cards/DashboardSidebarCard";
import { NodeHealthStripCard } from "./features/dashboard/cards/NodeHealthStripCard";
import { OverviewDashboardSection } from "./features/dashboard/OverviewDashboardSection";
import { VoiceEndpointDashboardSection } from "./features/dashboard/VoiceEndpointDashboardSection";
import { VoiceIntentsDashboardSection } from "./features/dashboard/VoiceIntentsDashboardSection";
import { TtsProviderDashboardSection } from "./features/dashboard/TtsProviderDashboardSection";
import { RuntimeDashboardSection } from "./features/dashboard/RuntimeDashboardSection";
import { PlaceholderDashboardSection } from "./features/dashboard/PlaceholderDashboardSection";
import { MigrationDashboardSection } from "./features/dashboard/MigrationDashboardSection";

const CANONICAL_SETUP_STEPS = [
  { id: "node_identity", label: "Node Identity" },
  { id: "core_connection", label: "Core Connection" },
  { id: "bootstrap_discovery", label: "Bootstrap Discovery" },
  { id: "registration", label: "Registration" },
  { id: "approval", label: "Approval" },
  { id: "trust_activation", label: "Trust Activation" },
  { id: "provider_setup", label: "Provider Setup" },
  { id: "capability_declaration", label: "Capability Declaration" },
  { id: "governance_sync", label: "Governance Sync" },
  { id: "ready", label: "Ready" },
];

const VOICE_ENDPOINT_REFRESH_MS = 2000;
const VOICE_INTENTS_REFRESH_MS = 5000;
const RUNTIME_REFRESH_MS = 2000;

function isSetupStage(onboarding, status) {
  const stepId = onboarding?.current_step_id || status?.current_step_id || "node_identity";
  return stepId !== "ready";
}

function parseRouteView(hash) {
  return typeof hash === "string" && hash.startsWith("#/dashboard") ? "dashboard" : "setup";
}

function parseDashboardSection(hash) {
  if (typeof hash !== "string" || !hash.startsWith("#/dashboard")) {
    return "overview";
  }
  const [, , section] = hash.split("/");
  return section || "overview";
}

function parseSetupSection(location) {
  if (!location) {
    return "flow";
  }
  if (location.pathname === "/setup/host" || location.hash === "#/setup/host") {
    return "host";
  }
  if (location.pathname === "/setup/core" || location.hash === "#/setup/core") {
    return "core";
  }
  if (location.pathname === "/setup/migration" || location.hash === "#/setup/migration") {
    return "migration";
  }
  return "flow";
}

function setHashRoute(view) {
  if (typeof window === "undefined") {
    return;
  }
  const hash = view === "dashboard" ? "#/dashboard" : "#/setup";
  if (window.location.hash !== hash) {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${hash}`);
  }
}

function setDashboardHashRoute(section = "overview") {
  if (typeof window === "undefined") {
    return;
  }
  const hash = `#/dashboard/${section}`;
  if (window.location.hash !== hash) {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${hash}`);
  }
}

function requiredSetupInputs(status) {
  const missing = [];
  if (!status?.node_name || status.node_name === "hexevoice") {
    missing.push("node_name");
  }
  if (status?.current_step_id === "node_identity") {
    missing.push("core_base_url");
  }
  return [...new Set(missing)];
}

function nodeStateFromStatus(status, onboarding) {
  if (status?.operational_ready) {
    return { tone: "success", label: "Ready" };
  }
  if (status?.trust_state === "trusted") {
    return { tone: "success", label: "Trusted" };
  }
  if (status?.trust_state === "revoked") {
    return { tone: "danger", label: "Revoked" };
  }
  if (onboarding?.current_step_label) {
    return { tone: "warning", label: onboarding.current_step_label };
  }
  return { tone: "neutral", label: "Loading" };
}

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

function buildSetupFlow(onboarding, status) {
  const stepMap = new Map((onboarding?.steps || []).map((step) => [step.step_id, step]));
  const operationalReady = Boolean(status?.operational_ready || onboarding?.operational_ready);
  const currentStepId = operationalReady
    ? "ready"
    : onboarding?.steps?.find((step) => step.current)?.step_id ||
      onboarding?.current_step_id ||
      "node_identity";

  const current = CANONICAL_SETUP_STEPS.find((step) => step.id === currentStepId) || CANONICAL_SETUP_STEPS[0];

  return {
    current: current ? { id: current.id, label: current.label } : null,
    steps: CANONICAL_SETUP_STEPS.map((step) => {
      const payload = stepMap.get(step.id);
      return {
        id: step.id,
        label: payload?.label || step.label,
        current: payload?.current || step.id === currentStepId,
        complete: payload?.complete || (operationalReady && step.id === "ready") || false,
      };
    }),
  };
}

export default function App() {
  const [status, setStatus] = useState(null);
  const [onboarding, setOnboarding] = useState(null);
  const [providerSetup, setProviderSetup] = useState(null);
  const [capabilities, setCapabilities] = useState(null);
  const [governance, setGovernance] = useState(null);
  const [operational, setOperational] = useState(null);
  const [voiceStatus, setVoiceStatus] = useState(null);
  const [servicesStatus, setServicesStatus] = useState(null);
  const [voiceIntents, setVoiceIntents] = useState(null);
  const [ttsSettings, setTtsSettings] = useState(null);
  const [endpointStatus, setEndpointStatus] = useState(null);
  const [error, setError] = useState("");
  const [restartingSetup, setRestartingSetup] = useState(false);
  const [routeView, setRouteView] = useState(() =>
    parseRouteView(typeof window !== "undefined" ? window.location.hash : ""),
  );
  const [dashboardSection, setDashboardSection] = useState(() =>
    parseDashboardSection(typeof window !== "undefined" ? window.location.hash : ""),
  );
  const [setupSection, setSetupSection] = useState(() =>
    parseSetupSection(typeof window !== "undefined" ? window.location : null),
  );
  const setupComplete = !isSetupStage(onboarding, status);
  const showSetupPage = !setupComplete || routeView === "setup";
  const setupFlow = buildSetupFlow(onboarding, status);
  const inSetup = showSetupPage;
  const nodeState = nodeStateFromStatus(status, onboarding);
  const requiredInputs = requiredSetupInputs(status);

  const refresh = useCallback(async () => {
    const [
      statusPayload,
      onboardingPayload,
      providerPayload,
      capabilityPayload,
      governancePayload,
      operationalPayload,
      voicePayload,
      servicesPayload,
      voiceIntentPayload,
      ttsSettingsPayload,
      endpointPayload,
    ] = await Promise.all([
      getNodeStatus(),
      getOnboardingStatus(),
      getProviderSetup().catch(() => null),
      getCapabilities().catch(() => null),
      getGovernanceCurrent().catch(() => null),
      getOperationalStatus().catch(() => null),
      getVoiceStatus().catch(() => null),
      getServicesStatus().catch(() => null),
      getVoiceIntents().catch(() => null),
      getTtsSettings().catch(() => null),
      getEndpointStatus().catch(() => null),
    ]);
    setStatus(statusPayload);
    setOnboarding(onboardingPayload);
    setProviderSetup(providerPayload);
    setCapabilities(capabilityPayload);
    setGovernance(governancePayload);
    setOperational(operationalPayload);
    setVoiceStatus(voicePayload);
    setServicesStatus(servicesPayload);
    setVoiceIntents(voiceIntentPayload);
    setTtsSettings(ttsSettingsPayload);
    setEndpointStatus(endpointPayload);
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
      getVoiceStatus().catch(() => null),
      getServicesStatus().catch(() => null),
      getVoiceIntents().catch(() => null),
      getTtsSettings().catch(() => null),
      getEndpointStatus().catch(() => null),
    ])
      .then(
        ([
          statusPayload,
          onboardingPayload,
          providerPayload,
          capabilityPayload,
          governancePayload,
          operationalPayload,
          voicePayload,
          servicesPayload,
          voiceIntentPayload,
          ttsSettingsPayload,
          endpointPayload,
        ]) => {
        if (!mounted) {
          return;
        }
        setStatus(statusPayload);
        setOnboarding(onboardingPayload);
        setProviderSetup(providerPayload);
        setCapabilities(capabilityPayload);
        setGovernance(governancePayload);
        setOperational(operationalPayload);
        setVoiceStatus(voicePayload);
        setServicesStatus(servicesPayload);
        setVoiceIntents(voiceIntentPayload);
        setTtsSettings(ttsSettingsPayload);
        setEndpointStatus(endpointPayload);
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

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }

    function syncRoute() {
      setRouteView(parseRouteView(window.location.hash));
      setDashboardSection(parseDashboardSection(window.location.hash));
      setSetupSection(parseSetupSection(window.location));
    }

    window.addEventListener("hashchange", syncRoute);
    window.addEventListener("popstate", syncRoute);
    syncRoute();
    return () => {
      window.removeEventListener("hashchange", syncRoute);
      window.removeEventListener("popstate", syncRoute);
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    if (setupComplete) {
      return;
    }
    setHashRoute("setup");
    if (routeView !== "setup") {
      setRouteView("setup");
    }
  }, [setupComplete, routeView]);

  useEffect(() => {
    if (showSetupPage || dashboardSection !== "voice-endpoint") {
      return undefined;
    }

    let mounted = true;

    async function refreshVisibleEndpoint() {
      try {
        const [voicePayload, endpointPayload] = await Promise.all([
          getVoiceStatus().catch(() => null),
          getEndpointStatus().catch(() => null),
        ]);
        if (!mounted) {
          return;
        }
        setVoiceStatus(voicePayload);
        setEndpointStatus(endpointPayload);
      } catch (err) {
        if (mounted) {
          setError(String(err.message || err));
        }
      }
    }

    refreshVisibleEndpoint();
    const timer = window.setInterval(refreshVisibleEndpoint, VOICE_ENDPOINT_REFRESH_MS);

    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [dashboardSection, showSetupPage]);

  useEffect(() => {
    if (showSetupPage || dashboardSection !== "runtime") {
      return undefined;
    }

    let mounted = true;

    async function refreshVisibleRuntime() {
      try {
        const [voicePayload, servicesPayload] = await Promise.all([
          getVoiceStatus().catch(() => null),
          getServicesStatus().catch(() => null),
        ]);
        if (!mounted) {
          return;
        }
        setVoiceStatus(voicePayload);
        setServicesStatus(servicesPayload);
      } catch (err) {
        if (mounted) {
          setError(String(err.message || err));
        }
      }
    }

    refreshVisibleRuntime();
    const timer = window.setInterval(refreshVisibleRuntime, RUNTIME_REFRESH_MS);

    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [dashboardSection, showSetupPage]);

  useEffect(() => {
    if (showSetupPage || dashboardSection !== "intents") {
      return undefined;
    }

    let mounted = true;

    async function refreshVisibleIntents() {
      try {
        const intentPayload = await getVoiceIntents().catch(() => null);
        if (!mounted) {
          return;
        }
        setVoiceIntents(intentPayload);
      } catch (err) {
        if (mounted) {
          setError(String(err.message || err));
        }
      }
    }

    refreshVisibleIntents();
    const timer = window.setInterval(refreshVisibleIntents, VOICE_INTENTS_REFRESH_MS);

    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [dashboardSection, showSetupPage]);

  async function handleRestartSetup() {
    setRestartingSetup(true);
    try {
      await restartOnboardingSetup();
      await refresh();
      setHashRoute("setup");
      setRouteView("setup");
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setRestartingSetup(false);
    }
  }

  function openDashboard() {
    setDashboardHashRoute("overview");
    setRouteView("dashboard");
    setDashboardSection("overview");
  }

  function openSetup() {
    setHashRoute("setup");
    setRouteView("setup");
  }

  function openDashboardSection(section) {
    setDashboardHashRoute(section);
    setRouteView("dashboard");
    setDashboardSection(section);
  }

  function renderDashboardSection() {
    if (dashboardSection === "voice-endpoint") {
      return (
        <VoiceEndpointDashboardSection
          status={status}
          providerSetup={providerSetup}
          capabilities={capabilities}
          voiceStatus={voiceStatus}
          endpointStatus={endpointStatus}
          onRefresh={refresh}
        />
      );
    }

    if (dashboardSection === "intents") {
      return <VoiceIntentsDashboardSection voiceIntents={voiceIntents} onRefresh={refresh} />;
    }

    if (dashboardSection === "providers") {
      return (
        <TtsProviderDashboardSection
          providerSetup={providerSetup}
          capabilities={capabilities}
          ttsSettings={ttsSettings}
          onRefresh={refresh}
        />
      );
    }

    if (dashboardSection === "runtime") {
      return (
        <RuntimeDashboardSection
          servicesStatus={servicesStatus}
          voiceStatus={voiceStatus}
          onRefresh={refresh}
        />
      );
    }

    if (dashboardSection === "diagnostics") {
      return (
        <PlaceholderDashboardSection
          title="Diagnostics"
          copy="Diagnostics, export tools, and deep inspection will live here next."
        />
      );
    }

    if (dashboardSection === "migration") {
      return <MigrationDashboardSection onRefresh={refresh} />;
    }

    return (
      <OverviewDashboardSection
        status={status}
        onboarding={onboarding}
        governance={governance}
        operational={operational}
        openSetup={openSetup}
        openVoiceEndpoint={() => openDashboardSection("voice-endpoint")}
        onRefresh={refresh}
      />
    );
  }

  return (
    <div className="shell">
      <main className="app-frame">
        <SetupHeroCard
          nodeState={nodeState}
          onboarding={onboarding}
          status={status}
          restartSetup={handleRestartSetup}
          restartingSetup={restartingSetup}
          dashboardEnabled={setupComplete}
          openDashboard={openDashboard}
          openProvider={() => {}}
        />

        <section className="app-shell">
          {showSetupPage ? (
            <SetupSidebar flow={setupFlow} />
          ) : (
            <DashboardSidebarCard dashboardSection={dashboardSection} openDashboard={openDashboardSection} />
          )}

          <div className="main-column">
            <section className="content-stack">
              {showSetupPage ? (
                setupSection === "host" ? (
                  <HostSetupPage />
                ) : setupSection === "core" ? (
                  <CoreSetupPage />
                ) : setupSection === "migration" ? (
                  <MigrationSetupPage />
                ) : (
                  <>
                    <OnboardingPanel status={status} onboarding={onboarding} onRefresh={refresh} />
                    <section className="grid setup-secondary-grid">
                      <LiveStatusCard status={status} />
                      <OperatorPromptsCard
                        requiredInputs={requiredInputs}
                        onboarding={onboarding}
                        status={status}
                        setupFlow={setupFlow}
                      />
                    </section>
                  </>
                )
              ) : (
                <>
                  <NodeHealthStripCard
                    status={status}
                    onboarding={onboarding}
                    providerSetup={providerSetup}
                    governance={governance}
                    operational={operational}
                    voiceStatus={voiceStatus}
                  />
                  {renderDashboardSection()}
                </>
              )}
            </section>
          </div>
        </section>
      </main>
    </div>
  );
}
