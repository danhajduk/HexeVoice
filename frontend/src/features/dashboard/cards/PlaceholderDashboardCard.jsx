export function PlaceholderDashboardCard({ title, copy }) {
  return (
    <section className="card stack panel placeholder-card">
      <div className="section-heading">
        <div>
          <p className="panel-kicker">Placeholder</p>
          <h2 className="panel-title">{title}</h2>
        </div>
      </div>
      <div className="callout">{copy}</div>
    </section>
  );
}
