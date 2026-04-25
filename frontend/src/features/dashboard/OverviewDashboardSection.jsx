import { CoreConnectionCard } from "./cards/CoreConnectionCard";
import { DashboardActionsCard } from "./cards/DashboardActionsCard";
import { NodeOverviewCard } from "./cards/NodeOverviewCard";
import { OperationalWarningsCard } from "./cards/OperationalWarningsCard";

export function OverviewDashboardSection({
  status,
  onboarding,
  governance,
  operational,
  openSetup,
  openVoiceEndpoint,
  onRefresh,
}) {
  return (
    <section className="grid operational-dashboard-grid">
      <OperationalWarningsCard status={status} onboarding={onboarding} />
      <NodeOverviewCard status={status} onboarding={onboarding} operational={operational} />
      <CoreConnectionCard status={status} onboarding={onboarding} governance={governance} operational={operational} />
      <DashboardActionsCard openSetup={openSetup} openVoiceEndpoint={openVoiceEndpoint} onRefresh={onRefresh} />
    </section>
  );
}
