export function GoSign({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#ef5018] font-cabin text-[10px] font-semibold uppercase tracking-[0.08em] text-white shadow-[0_10px_30px_rgba(239,80,24,0.2)] ${className}`}
      title="Go — approved to bid"
      aria-label="Go"
    >
      Go
    </span>
  );
}
