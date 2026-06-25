import type { RfpRecord } from "@/types/rfp";

/** Local dev fallback when PDF is still on disk (no Supabase). */
export function rfpPdfProxyHref(rfpId: string): string {
  return `/api/rfps/${encodeURIComponent(rfpId)}/pdf`;
}

export function hasRfpPdf(rfp: Pick<RfpRecord, "pdfPath">): boolean {
  return Boolean(rfp.pdfPath?.trim());
}

/** Dashboard links use the Next.js PDF proxy; signing happens only when opening a PDF. */
export function withDashboardPdfUrl(rfp: RfpRecord): RfpRecord {
  if (!hasRfpPdf(rfp)) {
    return { ...rfp, pdfUrl: undefined };
  }
  if (rfp.pdfUrl?.startsWith("http")) {
    return rfp;
  }
  return { ...rfp, pdfUrl: rfpPdfProxyHref(rfp.id) };
}
