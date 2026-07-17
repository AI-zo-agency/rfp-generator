import { backendFetch } from "@/lib/backend-api";
import { NextResponse } from "next/server";

export async function POST(request: Request) {
  try {
    const formData = await request.formData();
    const response = await backendFetch("/rfps/extract-due-date", {
      method: "POST",
      body: formData,
    });

    const text = await response.text();
    let data: unknown = {};
    if (text.trim()) {
      try {
        data = JSON.parse(text);
      } catch {
        return NextResponse.json(
          { error: "Invalid JSON from backend" },
          { status: 502 }
        );
      }
    }

    if (!response.ok) {
      const detail =
        typeof data === "object" && data && "detail" in data
          ? String((data as { detail: unknown }).detail)
          : "Failed to extract due date";
      return NextResponse.json({ error: detail }, { status: response.status });
    }

    return NextResponse.json(data);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to extract due date.";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
