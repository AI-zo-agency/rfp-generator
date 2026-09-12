export default function AnalyticsLoading() {
  return (
    <div className="space-y-12 p-2">
      <div>
        <div className="h-8 w-40 rounded bg-zo-surface-secondary" />
        <div className="mt-3 h-4 w-72 rounded bg-zo-surface-secondary" />
      </div>
      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="zo-card h-28 animate-pulse bg-zo-surface-secondary/40" />
        ))}
      </div>
      <div className="zo-card h-40 animate-pulse bg-zo-surface-secondary/40" />
    </div>
  );
}
