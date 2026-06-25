export function RfpTableSkeleton() {
  return (
    <div className="zo-card animate-pulse overflow-hidden">
      <div className="border-b border-zo-border px-6 py-4">
        <div className="flex gap-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-9 w-20 rounded-lg bg-zo-warm-gray/70" />
          ))}
        </div>
      </div>
      <div className="divide-y divide-zo-border/60">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex items-center gap-4 px-6 py-5">
            <div className="h-10 w-10 shrink-0 rounded-full bg-zo-warm-gray/60" />
            <div className="min-w-0 flex-1 space-y-2">
              <div className="h-4 w-2/3 rounded bg-zo-warm-gray" />
              <div className="h-3 w-1/3 rounded bg-zo-warm-gray/60" />
            </div>
            <div className="hidden h-8 w-24 rounded-lg bg-zo-warm-gray/50 sm:block" />
          </div>
        ))}
      </div>
    </div>
  );
}
