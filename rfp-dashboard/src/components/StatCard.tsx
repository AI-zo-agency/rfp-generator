interface StatCardProps {
  label: string;
  value: string | number;
  subtitle?: string;
  accent?: "orange" | "teal" | "black";
}

const accentColors = {
  orange: "border-zo-orange",
  teal: "border-zo-teal",
  black: "border-zo-black",
};

export function StatCard({
  label,
  value,
  subtitle,
  accent = "orange",
}: StatCardProps) {
  return (
    <div className={`zo-card overflow-hidden rounded-2xl border-l-[5px] p-8 ${accentColors[accent]}`}>
      <p className="text-[11px] font-bold uppercase tracking-[0.15em] text-zo-text-muted">
        {label}
      </p>
      <p className="font-heading mt-4 text-5xl font-bold tracking-tight text-foreground">
        {value}
      </p>
      {subtitle && (
        <p className="mt-3 text-sm leading-relaxed text-zo-text-muted">
          {subtitle}
        </p>
      )}
    </div>
  );
}
