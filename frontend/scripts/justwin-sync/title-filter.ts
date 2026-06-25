const DEFAULT_TITLE_FILTER =
  "Advertising, Marketing, Communications for Tennessee Board of Regents";

export function getTitleFilter(): string | null {
  const raw = process.env.JUSTWIN_RFP_TITLE_FILTER?.trim();
  if (raw === "false" || raw === "off" || raw === "*") {
    return null;
  }
  return raw || DEFAULT_TITLE_FILTER;
}

export function matchesTitleFilter(title: string, filter: string): boolean {
  const normalizedTitle = title.replace(/\s+/g, " ").trim().toLowerCase();
  const normalizedFilter = filter.replace(/\s+/g, " ").trim().toLowerCase();
  return normalizedTitle.includes(normalizedFilter);
}
