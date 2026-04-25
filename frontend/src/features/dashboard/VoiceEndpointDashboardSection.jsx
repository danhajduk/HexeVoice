import { PlaceholderDashboardCard } from "./cards/PlaceholderDashboardCard";
import { VoiceEndpointActionsCard } from "./cards/VoiceEndpointActionsCard";
import { VoiceEndpointStatusCard } from "./cards/VoiceEndpointStatusCard";

export function VoiceEndpointDashboardSection({ status, providerSetup, capabilities, onRefresh }) {
  return (
    <section className="grid operational-dashboard-grid">
      <VoiceEndpointStatusCard status={status} providerSetup={providerSetup} capabilities={capabilities} />
      <VoiceEndpointActionsCard onRefresh={onRefresh} />
      <PlaceholderDashboardCard
        title="Speech Pipeline"
        copy="Placeholder for microphone, STT, TTS, wake-word, and audio-path visibility."
      />
      <PlaceholderDashboardCard
        title="Device Sessions"
        copy="Placeholder for endpoint session history, recent turns, and transport health."
      />
    </section>
  );
}
