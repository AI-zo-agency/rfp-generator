export function RfpDetailSkeleton() {
  return (
    <div className="animate-pulse space-y-10">
      <div className="space-y-3">
        <div className="h-4 w-32 rounded bg-zo-warm-gray/60" />
        <div className="h-10 w-3/4 max-w-xl rounded-lg bg-zo-warm-gray" />
        <div className="h-5 w-1/2 max-w-md rounded bg-zo-warm-gray/60" />
      </div>

      <section className="zo-card space-y-6 p-8">
        <div className="flex flex-wrap gap-3">
          <div className="h-8 w-24 rounded-full bg-zo-warm-gray/70" />
          <div className="h-8 w-28 rounded-full bg-zo-warm-gray/60" />
          <div className="h-8 w-32 rounded-full bg-zo-warm-gray/50" />
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="space-y-2">
              <div className="h-3 w-16 rounded bg-zo-warm-gray/50" />
              <div className="h-5 w-28 rounded bg-zo-warm-gray/70" />
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-3 border-t border-zo-border pt-6">
          <div className="h-10 w-36 rounded-xl bg-zo-warm-gray/60" />
          <div className="h-10 w-32 rounded-xl bg-zo-warm-gray/50" />
        </div>
      </section>
    </div>
  );
}
