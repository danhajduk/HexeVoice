export function OnboardingPanel({ status }) {
  return (
    <section className="panel">
      <h2>Onboarding</h2>
      <p>Current stage: <strong>{status?.lifecycle_state || "bootstrap_required"}</strong></p>
      <p>Trust state: <strong>{status?.trust_state || "untrusted"}</strong></p>
    </section>
  );
}
