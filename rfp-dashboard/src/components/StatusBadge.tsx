import type { GoNoGoRecommendation, RfpPriority, RfpStatus } from "@/types/rfp";

const statusStyles: Record<RfpStatus, string> = {
  new: "border-zo-border text-zo-text-secondary",
  active: "bg-[#274742] border-transparent text-white",
  pending_approval: "bg-[#ffd652] border-transparent text-black",
  in_progress: "bg-[#ff8939] border-transparent text-white",
  review: "bg-[#274742] border-transparent text-white",
  submitted: "bg-[#ef5018] border-transparent text-white",
  won: "bg-[#7bdcb5] border-transparent text-black",
  lost: "bg-[#cf2e2e] border-transparent text-white",
  passed: "border-zo-border text-zo-text-muted",
};

const statusLabels: Record<RfpStatus, string> = {
  new: "New",
  active: "Active",
  pending_approval: "Pending",
  in_progress: "In Progress",
  review: "Review",
  submitted: "Submitted",
  won: "Won",
  lost: "Lost",
  passed: "Passed",
};

export function StatusBadge({ status }: { status: RfpStatus }) {
  return (
    <span className={`zo-tag ${statusStyles[status]}`}>
      {statusLabels[status]}
    </span>
  );
}

export function GoNoGoBadge({
  recommendation,
}: {
  recommendation: GoNoGoRecommendation;
}) {
  if (!recommendation) {
    return (
      <span className="zo-tag text-zo-text-muted">—</span>
    );
  }

  const styles = {
    go: "bg-[#ef5018] border-transparent text-white",
    no_go: "bg-[#cf2e2e] border-transparent text-white",
    review: "bg-[#ffd652] border-transparent text-black",
  };

  const labels = {
    go: "Go",
    no_go: "No-Go",
    review: "Review",
  };

  return (
    <span className={`zo-tag ${styles[recommendation]}`}>
      {labels[recommendation]}
    </span>
  );
}

const priorityStyles: Record<RfpPriority, string> = {
  critical: "bg-[#cf2e2e] border-transparent text-white",
  high: "bg-[#ef5018] border-transparent text-white",
  medium: "bg-[#ffd652] border-transparent text-black",
  low: "border-zo-border text-zo-text-muted",
};

export function PriorityBadge({ priority }: { priority: RfpPriority }) {
  return (
    <span className={`zo-tag ${priorityStyles[priority]}`}>
      {priority}
    </span>
  );
}
