/**
 * Client-side guard for the "opened this RFP" beacon. The backend throttles
 * per person per RFP (30 minutes); this only stops React re-mounts and
 * double effects from sending the same open twice in a row.
 */
export const OPEN_CLIENT_DEBOUNCE_MS = 60_000;

export function shouldSendOpen(
  rfpId: string,
  lastSent: Record<string, number>,
  now: number,
): boolean {
  const previous = lastSent[rfpId];
  return previous === undefined || now - previous > OPEN_CLIENT_DEBOUNCE_MS;
}
