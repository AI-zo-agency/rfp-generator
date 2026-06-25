import type { TeamMember } from "@/types/rfp";

interface TeamWorkloadProps {
  team: TeamMember[];
}

export function TeamWorkload({ team }: TeamWorkloadProps) {
  return (
    <section className="zo-card p-8">
      <h2 className="font-heading text-lg font-bold text-foreground">
        Writer Workload
      </h2>
      <p className="mt-1 text-sm text-zo-text-muted">
        Target · 12 submissions per writer / month
      </p>

      <div className="mt-8 space-y-6">
        {team.map((member) => {
          const pct = Math.min(
            Math.round((member.activeCount / member.capacity) * 100),
            100
          );
          const atCapacity = member.activeCount >= member.capacity;

          return (
            <div key={member.name}>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-foreground">
                    {member.name}
                  </p>
                  <p className="text-xs text-zo-text-muted">{member.role}</p>
                </div>
                <p className="text-sm font-bold text-zo-text-secondary">
                  {member.activeCount}
                  <span className="font-normal text-zo-text-muted">
                    /{member.capacity}
                  </span>
                </p>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-zo-warm-gray">
                <div
                  className={`h-full origin-left rounded-full ${atCapacity ? "bg-zo-orange" : "bg-zo-teal"}`}
                  style={{
                    transform: `scaleX(${pct / 100})`,
                    transition: "transform 0.6s var(--ease-smooth)",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
