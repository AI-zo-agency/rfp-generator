import type { OutlineSectionStatus } from "@/types/proposal";

const statusConfig: Record<
  OutlineSectionStatus,
  { label: string; className: string }
> = {
  empty: {
    label: "Empty",
    className: "bg-zo-warm-gray/80 text-zo-text-muted",
  },
  outline: {
    label: "Outline",
    className: "bg-[var(--zo-surface)] text-zo-text-secondary ring-1 ring-zo-border",
  },
  generated: {
    label: "Generated",
    className:
      "bg-emerald-500/12 text-emerald-700 ring-1 ring-emerald-500/20",
  },
  reviewed: {
    label: "Reviewed",
    className: "bg-zo-teal/10 text-zo-teal ring-1 ring-zo-teal/20",
  },
};

export function SectionStatusPill({
  status,
}: {
  status: OutlineSectionStatus;
}) {
  const config = statusConfig[status];
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${config.className}`}
    >
      {config.label}
    </span>
  );
}
