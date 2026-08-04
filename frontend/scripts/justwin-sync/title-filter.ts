export function getTitleFilter(): string | null {
  const raw = process.env.JUSTWIN_RFP_TITLE_FILTER?.trim();
  if (!raw || raw === "false" || raw === "off" || raw === "*") {
    return null;
  }
  return raw;
}

export function matchesTitleFilter(title: string, filter: string): boolean {
  const normalizedTitle = title.replace(/\s+/g, " ").trim().toLowerCase();
  const normalizedFilter = filter.replace(/\s+/g, " ").trim().toLowerCase();
  return normalizedTitle.includes(normalizedFilter);
}
