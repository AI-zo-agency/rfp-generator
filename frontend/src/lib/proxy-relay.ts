import { NextResponse } from "next/server";

/** Relay a backend response as JSON. A non-JSON body (a proxy page, a plain-text 500) becomes a readable error. */
export async function relayJson(res: Response): Promise<NextResponse> {
  const text = await res.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: `The server answered with an unexpected response (${res.status}). Try again.` };
  }
  return NextResponse.json(data, { status: res.status });
}

export function unreachable(error: unknown): NextResponse {
  const message = error instanceof Error && error.message ? error.message : "Backend unreachable";
  return NextResponse.json({ error: message }, { status: 503 });
}

/** Parse a JSON request body. On failure `response` is a 400 to return as is. */
export async function readJson(
  request: Request
): Promise<{ body: unknown; response?: undefined } | { body?: undefined; response: NextResponse }> {
  try {
    return { body: await request.json() };
  } catch {
    return { response: NextResponse.json({ error: "Invalid JSON body" }, { status: 400 }) };
  }
}
