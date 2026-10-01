import { describe, expect, test } from "vitest";
import { readJson, relayJson, unreachable } from "./proxy-relay";

describe("relayJson", () => {
  test("passes a JSON body and its status through", async () => {
    const out = await relayJson(new Response(JSON.stringify({ detail: "Not found" }), { status: 404 }));
    expect(out.status).toBe(404);
    expect(await out.json()).toEqual({ detail: "Not found" });
  });

  test("turns a plain-text 500 into a readable JSON error", async () => {
    const out = await relayJson(new Response("Internal Server Error", { status: 500 }));
    expect(out.status).toBe(500);
    expect(((await out.json()) as { error: string }).error).toContain("(500)");
  });

  test("an empty body is an empty object", async () => {
    const out = await relayJson(new Response("", { status: 200 }));
    expect(await out.json()).toEqual({});
  });
});

describe("readJson", () => {
  test("parses a valid body", async () => {
    const req = new Request("http://x", { method: "PUT", body: JSON.stringify({ a: 1 }) });
    expect((await readJson(req)).body).toEqual({ a: 1 });
  });

  test("a malformed body is a 400", async () => {
    const out = await readJson(new Request("http://x", { method: "PUT", body: "{nope" }));
    expect(out.response?.status).toBe(400);
  });
});

test("unreachable is a 503 with the error message", async () => {
  const out = unreachable(new Error("ECONNREFUSED"));
  expect(out.status).toBe(503);
  expect(await out.json()).toEqual({ error: "ECONNREFUSED" });
});
