export function DashboardSidebarCard({ dashboardSection, openDashboard }) {
  return (
    <aside className="card operational-shell-nav-card">
      <nav className="operational-shell-nav" aria-label="Operational sections">
        <button
          type="button"
          className={`btn operational-nav-btn ${dashboardSection === "overview" ? "btn-primary" : ""}`}
          onClick={() => openDashboard("overview")}
        >
          Overview
        </button>
        <button
          type="button"
          className={`btn operational-nav-btn ${dashboardSection === "voice-endpoint" ? "btn-primary" : ""}`}
          onClick={() => openDashboard("voice-endpoint")}
        >
          Voice Endpoint
        </button>
        <button
          type="button"
          className={`btn operational-nav-btn ${dashboardSection === "intents" ? "btn-primary" : ""}`}
          onClick={() => openDashboard("intents")}
        >
          Intents
        </button>
        <button
          type="button"
          className={`btn operational-nav-btn ${dashboardSection === "providers" ? "btn-primary" : ""}`}
          onClick={() => openDashboard("providers")}
        >
          Providers
        </button>
        <button
          type="button"
          className={`btn operational-nav-btn ${dashboardSection === "runtime" ? "btn-primary" : ""}`}
          onClick={() => openDashboard("runtime")}
        >
          Runtime
        </button>
        <button
          type="button"
          className={`btn operational-nav-btn ${dashboardSection === "diagnostics" ? "btn-primary" : ""}`}
          onClick={() => openDashboard("diagnostics")}
        >
          Diagnostics
        </button>
        <button
          type="button"
          className={`btn operational-nav-btn ${dashboardSection === "migration" ? "btn-primary" : ""}`}
          onClick={() => openDashboard("migration")}
        >
          Migration
        </button>
        <button type="button" className="btn operational-nav-btn">
          Activity
        </button>
      </nav>
    </aside>
  );
}
