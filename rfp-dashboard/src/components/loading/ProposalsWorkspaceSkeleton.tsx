export function ProposalsWorkspaceSkeleton() {
  return (
    <div className="grid items-start gap-6 xl:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="proposal-go-sidebar animate-pulse overflow-hidden">
        <div className="border-b border-zo-border/80 bg-[#fafbfc] px-5 py-5">
          <div className="h-4 w-20 rounded bg-zo-warm-gray/70" />
          <div className="mt-2 h-4 w-32 rounded bg-zo-warm-gray/50" />
          <div className="mt-4 h-10 w-full rounded-xl bg-zo-warm-gray/60" />
        </div>
        <div className="divide-y divide-zo-border/50">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="space-y-2 px-5 py-4">
              <div className="h-4 w-full rounded bg-zo-warm-gray/70" />
              <div className="h-3 w-2/3 rounded bg-zo-warm-gray/50" />
            </div>
          ))}
        </div>
      </aside>

      <div className="proposal-workspace-card animate-pulse space-y-4 p-8">
        <div className="h-8 w-2/3 rounded-lg bg-zo-warm-gray" />
        <div className="h-4 w-1/2 rounded bg-zo-warm-gray/70" />
        <div className="mt-8 grid grid-cols-3 gap-4">
          <div className="h-20 rounded-xl bg-zo-warm-gray/60" />
          <div className="h-20 rounded-xl bg-zo-warm-gray/60" />
          <div className="h-20 rounded-xl bg-zo-warm-gray/60" />
        </div>
      </div>
    </div>
  );
}
