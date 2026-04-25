export function StageCard({ title, tone, children, action }) {
  return (
    <article className={`card stack stage-card tone-${tone}`}>
      <div className="section-heading">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </article>
  );
}
