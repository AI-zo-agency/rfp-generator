export function ProposalsWorkspaceSkeleton() {
  return (
    <div className="proposal-workspace-card animate-pulse space-y-4 p-6 md:p-8">
      <div className="h-8 w-2/3 rounded-lg bg-zo-warm-gray" />
      <div className="h-4 w-1/2 rounded bg-zo-warm-gray/70" />
      <div className="mt-8 grid grid-cols-3 gap-4">
        <div className="h-20 rounded-xl bg-zo-warm-gray/60" />
        <div className="h-20 rounded-xl bg-zo-warm-gray/60" />
        <div className="h-20 rounded-xl bg-zo-warm-gray/60" />
      </div>
      <div className="mt-6 h-64 rounded-xl bg-zo-warm-gray/40" />
    </div>
  );
}
