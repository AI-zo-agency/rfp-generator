/** Knowledge Base page palette — anchored on sidebar active nav (#ef5018). */
export const KB_BRAND = {
  primary: "#ef5018",
  primaryHover: "#d44312",
  primaryLight: "#ff6935",
  primarySoft: "rgba(239, 80, 24, 0.08)",
  primarySoftStrong: "rgba(239, 80, 24, 0.14)",
  primaryBorder: "rgba(239, 80, 24, 0.35)",
  primaryRing: "rgba(239, 80, 24, 0.25)",
  primaryDeep: "#b8360e",
  cream: "#fff4ef",
  shadow: "0 8px 24px rgba(239, 80, 24, 0.25)",
  shadowHover: "0 12px 32px rgba(239, 80, 24, 0.32)",
} as const;

export const kbBtnPrimary =
  "inline-flex items-center justify-center gap-2 rounded-[var(--zo-radius-md)] bg-[#ef5018] px-[18px] py-2.5 text-[13px] font-semibold uppercase tracking-[0.08em] text-white shadow-[0_8px_24px_rgba(239,80,24,0.25)] transition-all hover:-translate-y-0.5 hover:bg-[#d44312] hover:shadow-[0_12px_32px_rgba(239,80,24,0.32)] disabled:cursor-not-allowed disabled:opacity-60";

export const kbBtnSecondary =
  "inline-flex items-center justify-center gap-2 rounded-[var(--zo-radius-md)] border border-[rgba(239,80,24,0.35)] bg-[#fff4ef] px-[18px] py-2.5 text-[13px] font-semibold uppercase tracking-[0.08em] text-[#ef5018] transition-all hover:-translate-y-0.5 hover:border-[#ef5018] hover:bg-[rgba(239,80,24,0.14)] hover:shadow-[0_8px_20px_rgba(239,80,24,0.12)] disabled:cursor-not-allowed disabled:opacity-60";

export const kbChipActive =
  "border-[#ef5018] bg-[#ef5018] text-white shadow-[0_4px_14px_rgba(239,80,24,0.22)]";

export const kbChipIdle =
  "border-zo-border bg-white text-zo-text-secondary hover:border-[rgba(239,80,24,0.4)] hover:text-[#ef5018]";

export const kbChipEmpty =
  "border-zo-border/70 bg-[#fff4ef]/60 text-zo-text-muted hover:border-[rgba(239,80,24,0.3)]";

export const kbToggleActive =
  "bg-[#ef5018] text-white shadow-[0_4px_14px_rgba(239,80,24,0.22)]";

export const kbAccentText = "text-[#ef5018]";
export const kbAccentBgSoft = "bg-[rgba(239,80,24,0.1)]";
export const kbAccentBorderHover = "group-hover:border-[rgba(239,80,24,0.4)] group-hover:text-[#ef5018]";
