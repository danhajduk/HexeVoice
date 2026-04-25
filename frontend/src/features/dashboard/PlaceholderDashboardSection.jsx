import { PlaceholderDashboardCard } from "./cards/PlaceholderDashboardCard";

export function PlaceholderDashboardSection({ title, copy }) {
  return (
    <section className="grid operational-dashboard-grid">
      <PlaceholderDashboardCard title={title} copy={copy} />
    </section>
  );
}
