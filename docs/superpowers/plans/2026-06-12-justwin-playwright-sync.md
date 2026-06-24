# JustWin Playwright Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the dashboard "Sync JustWin" button to a Playwright scraper that logs into `app.justwin.ai`, pulls all solicitations (Hot/Warm/Review tabs), downloads PDFs, and displays them in the RFP dashboard.

**Architecture:** Playwright runs in a **separate Node worker** (`rfp-dashboard/scripts/justwin-sync/`), not inside Next.js API routes (Playwright is too heavy for serverless and needs a real browser). The dashboard triggers sync via `POST /api/justwin/sync`, which spawns the worker as a child process. Synced data is persisted in **SQLite** (`rfp-dashboard/data/rfps.db`) and PDFs in **`rfp-dashboard/storage/pdfs/`**. The existing `getRfps()` service reads from the DB first, falling back to mock data only when the DB is empty and no sync has run.

**Tech Stack:** Next.js 16 App Router, Playwright, better-sqlite3, TypeScript, child_process

---

## Context: What Exists Today

| Piece | Status |
|---|---|
| `Sync JustWin` button | UI only — no `onClick`, no API call (`DashboardHeader.tsx`, `TopBar.tsx`) |
| `getRfps()` | Tries `JUSTWIN_API_URL` + `JUSTWIN_API_KEY` (no real endpoint); falls back to `mockRfps` |
| `RfpRecord` type | Has `externalId`, `pdfUrl`, `source: "justwin"` — ready for sync |
| Database / file storage | None |
| Playwright | Not installed |

## JustWin UI Mapping (from `app.justwin.ai/leads`)

| JustWin field | Maps to `RfpRecord` |
|---|---|
| Title (link) | `title`, detail page URL → `externalId` (slug or ID from URL) |
| `[CA]` location tag | `location` |
| Posted date | `receivedDate` |
| Due date | `dueDate` |
| Score bars (1–5) | `fitScore` (multiply by 20 → 0–100 scale to match mock data) |
| Description snippet | stored in new `description` field (optional) |
| Tab (Hot/Warm/Review) | stored in new `justwinTab` field for filtering |
| PDF on detail page | downloaded → `pdfPath` (local) + `pdfUrl` (served via API) |

## Approach Comparison

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| **A. Playwright worker (recommended)** | Works without JustWin API; mirrors real UI; handles auth | Fragile if UI changes; needs session management | **Use this** |
| **B. JustWin REST API** | Stable, fast | Unknown if public API exists; code already stubs it | Ask JustWin; swap later |
| **C. Playwright inside API route** | Simpler deployment | Timeouts, no serverless, blocks request | Avoid |

---

## File Structure

```
rfp-dashboard/
├── data/
│   └── rfps.db                    # SQLite (gitignored)
├── storage/
│   └── pdfs/{externalId}/       # Downloaded PDFs (gitignored)
├── scripts/
│   └── justwin-sync/
│       ├── index.ts               # CLI entry: `npm run sync:justwin`
│       ├── browser.ts             # Launch browser, load/save session
│       ├── scrape-leads.ts        # Scrape /leads tabs
│       ├── scrape-detail.ts       # Visit detail page, find PDFs
│       ├── download-pdfs.ts       # Save PDFs to storage/
│       └── types.ts               # Raw JustWin scrape shapes
├── src/
│   ├── app/api/
│   │   ├── justwin/sync/route.ts  # POST — trigger sync
│   │   ├── justwin/status/route.ts# GET — sync job status
│   │   └── rfps/[id]/pdf/route.ts # GET — serve PDF file
│   ├── components/
│   │   ├── SyncJustWinButton.tsx  # Shared client component
│   │   ├── DashboardHeader.tsx    # Use SyncJustWinButton
│   │   └── TopBar.tsx             # Use SyncJustWinButton
│   ├── lib/
│   │   ├── db.ts                  # SQLite init + queries
│   │   ├── justwin-mapper.ts      # Raw → RfpRecord
│   │   ├── justwin-sync-runner.ts # Spawn worker, track status
│   │   └── rfp-service.ts         # Read from DB
│   └── types/rfp.ts               # Add description?, justwinTab?, pdfPath?
```

---

## Environment Variables

```bash
# .env.local (gitignored)
JUSTWIN_EMAIL=your@email.com
JUSTWIN_PASSWORD=your-password
JUSTWIN_BASE_URL=https://app.justwin.ai
JUSTWIN_SESSION_PATH=./data/justwin-session.json   # saved cookies after first login
DATABASE_PATH=./data/rfps.db
PDF_STORAGE_PATH=./storage/pdfs
```

---

### Task 1: Install dependencies and Playwright

**Files:**
- Modify: `rfp-dashboard/package.json`

- [ ] **Step 1: Add packages**

```bash
cd rfp-dashboard
npm install better-sqlite3
npm install -D @types/better-sqlite3 playwright tsx
npx playwright install chromium
```

- [ ] **Step 2: Add npm scripts to `package.json`**

```json
"sync:justwin": "tsx scripts/justwin-sync/index.ts",
"sync:justwin:debug": "HEADLESS=false tsx scripts/justwin-sync/index.ts"
```

- [ ] **Step 3: Update `.gitignore`**

```
data/
storage/
.env.local
```

- [ ] **Step 4: Commit**

```bash
git add package.json package-lock.json .gitignore
git commit -m "chore: add playwright and sqlite deps for JustWin sync"
```

---

### Task 2: Extend types and database schema

**Files:**
- Modify: `rfp-dashboard/src/types/rfp.ts`
- Create: `rfp-dashboard/src/lib/db.ts`
- Create: `rfp-dashboard/scripts/justwin-sync/types.ts`

- [ ] **Step 1: Extend `RfpRecord` in `src/types/rfp.ts`**

Add optional fields:

```typescript
description?: string;
justwinTab?: "hot" | "warm" | "review";
pdfPath?: string;       // local filesystem path
justwinDetailUrl?: string;
syncedAt?: string;
```

- [ ] **Step 2: Create `src/lib/db.ts`**

```typescript
import Database from "better-sqlite3";
import path from "path";
import type { RfpRecord } from "@/types/rfp";

const DB_PATH = process.env.DATABASE_PATH ?? path.join(process.cwd(), "data", "rfps.db");

let db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (!db) {
    const fs = require("fs");
    fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
    db = new Database(DB_PATH);
    db.exec(`
      CREATE TABLE IF NOT EXISTS rfps (
        id TEXT PRIMARY KEY,
        external_id TEXT UNIQUE,
        title TEXT NOT NULL,
        client TEXT,
        source TEXT DEFAULT 'justwin',
        sector TEXT,
        location TEXT,
        due_date TEXT,
        received_date TEXT,
        stage TEXT DEFAULT 'intake',
        status TEXT DEFAULT 'new',
        priority TEXT DEFAULT 'medium',
        fit_score INTEGER,
        worth_score INTEGER,
        go_no_go TEXT,
        assigned_to TEXT,
        estimated_value INTEGER,
        page_limit INTEGER,
        last_activity TEXT,
        last_activity_note TEXT,
        contract_role TEXT DEFAULT 'prime',
        description TEXT,
        justwin_tab TEXT,
        pdf_path TEXT,
        justwin_detail_url TEXT,
        synced_at TEXT
      );
      CREATE TABLE IF NOT EXISTS sync_jobs (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        rfps_found INTEGER DEFAULT 0,
        pdfs_downloaded INTEGER DEFAULT 0,
        error TEXT
      );
    `);
  }
  return db;
}

export function upsertRfp(rfp: RfpRecord): void {
  const d = getDb();
  d.prepare(`
    INSERT INTO rfps (id, external_id, title, client, source, sector, location,
      due_date, received_date, stage, status, priority, fit_score, worth_score,
      go_no_go, assigned_to, estimated_value, page_limit, last_activity,
      last_activity_note, contract_role, description, justwin_tab, pdf_path,
      justwin_detail_url, synced_at)
    VALUES (@id, @externalId, @title, @client, @source, @sector, @location,
      @dueDate, @receivedDate, @stage, @status, @priority, @fitScore, @worthScore,
      @goNoGo, @assignedTo, @estimatedValue, @pageLimit, @lastActivity,
      @lastActivityNote, @contractRole, @description, @justwinTab, @pdfPath,
      @justwinDetailUrl, @syncedAt)
    ON CONFLICT(external_id) DO UPDATE SET
      title=excluded.title, due_date=excluded.due_date, fit_score=excluded.fit_score,
      description=excluded.description, justwin_tab=excluded.justwin_tab,
      pdf_path=COALESCE(excluded.pdf_path, rfps.pdf_path),
      synced_at=excluded.synced_at
  `).run(rfp);
}

export function getAllRfps(): RfpRecord[] {
  const rows = getDb().prepare("SELECT * FROM rfps ORDER BY received_date DESC").all();
  return rows.map(rowToRfp);
}

function rowToRfp(row: Record<string, unknown>): RfpRecord {
  return {
    id: row.id as string,
    externalId: row.external_id as string,
    title: row.title as string,
    client: (row.client as string) ?? "",
    source: "justwin",
    sector: (row.sector as string) ?? "Public Sector",
    location: (row.location as string) ?? "",
    dueDate: row.due_date as string,
    receivedDate: row.received_date as string,
    stage: row.stage as RfpRecord["stage"],
    status: row.status as RfpRecord["status"],
    priority: row.priority as RfpRecord["priority"],
    fitScore: row.fit_score as number | null,
    worthScore: row.worth_score as number | null,
    goNoGo: row.go_no_go as RfpRecord["goNoGo"],
    assignedTo: row.assigned_to as string | null,
    estimatedValue: row.estimated_value as number | null,
    pageLimit: row.page_limit as number | undefined,
    lastActivity: row.last_activity as string,
    lastActivityNote: row.last_activity_note as string,
    contractRole: row.contract_role as RfpRecord["contractRole"],
    description: row.description as string | undefined,
    justwinTab: row.justwin_tab as RfpRecord["justwinTab"],
    pdfPath: row.pdf_path as string | undefined,
    justwinDetailUrl: row.justwin_detail_url as string | undefined,
    syncedAt: row.synced_at as string | undefined,
    pdfUrl: row.pdf_path ? `/api/rfps/${row.id}/pdf` : undefined,
  };
}
```

- [ ] **Step 3: Commit**

```bash
git add src/types/rfp.ts src/lib/db.ts scripts/justwin-sync/types.ts
git commit -m "feat: add sqlite schema and extended RfpRecord for JustWin sync"
```

---

### Task 3: Playwright browser session + login

**Files:**
- Create: `rfp-dashboard/scripts/justwin-sync/browser.ts`

- [ ] **Step 1: Implement session-aware browser launcher**

```typescript
import { chromium, type BrowserContext } from "playwright";
import fs from "fs";
import path from "path";

const BASE_URL = process.env.JUSTWIN_BASE_URL ?? "https://app.justwin.ai";
const SESSION_PATH = process.env.JUSTWIN_SESSION_PATH ?? "./data/justwin-session.json";

export async function getAuthenticatedContext(): Promise<BrowserContext> {
  const browser = await chromium.launch({
    headless: process.env.HEADLESS !== "false",
  });

  let context: BrowserContext;
  if (fs.existsSync(SESSION_PATH)) {
    const storage = JSON.parse(fs.readFileSync(SESSION_PATH, "utf-8"));
    context = await browser.newContext({ storageState: storage });
  } else {
    context = await browser.newContext();
  }

  const page = await context.newPage();
  await page.goto(`${BASE_URL}/leads`, { waitUntil: "networkidle" });

  // If redirected to login, authenticate
  if (page.url().includes("/login") || page.url().includes("/sign")) {
    const email = process.env.JUSTWIN_EMAIL;
    const password = process.env.JUSTWIN_PASSWORD;
    if (!email || !password) {
      throw new Error("JUSTWIN_EMAIL and JUSTWIN_PASSWORD required for first login");
    }
    await page.fill('input[type="email"], input[name="email"]', email);
    await page.fill('input[type="password"]', password);
    await page.click('button[type="submit"]');
    await page.waitForURL("**/leads**", { timeout: 30000 });
    fs.mkdirSync(path.dirname(SESSION_PATH), { recursive: true });
    await context.storageState({ path: SESSION_PATH });
  }

  await page.close();
  return context;
}
```

- [ ] **Step 2: Manual test first login**

```bash
cd rfp-dashboard
HEADLESS=false npm run sync:justwin:debug
```

Expected: Browser opens, logs in, saves `data/justwin-session.json`, reaches `/leads`.

- [ ] **Step 3: Commit**

```bash
git add scripts/justwin-sync/browser.ts
git commit -m "feat: add JustWin Playwright auth with session persistence"
```

---

### Task 4: Scrape leads list (all tabs)

**Files:**
- Create: `rfp-dashboard/scripts/justwin-sync/scrape-leads.ts`
- Create: `rfp-dashboard/scripts/justwin-sync/types.ts`

- [ ] **Step 1: Define raw scrape type in `types.ts`**

```typescript
export interface JustWinLead {
  externalId: string;
  title: string;
  location: string;
  postedDate: string;
  dueDate: string;
  score: number;        // 1-5 bars
  description: string;
  detailUrl: string;
  tab: "hot" | "warm" | "review";
}
```

- [ ] **Step 2: Implement tab scraper**

Selectors will need tuning after inspecting JustWin DOM. Start with resilient patterns:

```typescript
import type { Page } from "playwright";
import type { JustWinLead } from "./types";

const TABS = [
  { label: "Hot Leads", tab: "hot" as const },
  { label: "Warm Leads", tab: "warm" as const },
  { label: "Review", tab: "review" as const },
];

export async function scrapeAllLeads(page: Page): Promise<JustWinLead[]> {
  const all: JustWinLead[] = [];

  for (const { label, tab } of TABS) {
    await page.getByRole("tab", { name: new RegExp(label, "i") }).click();
    await page.waitForTimeout(1500); // let list render

    const rows = page.locator('[data-testid="lead-row"], table tbody tr, [class*="lead"]');
    const count = await rows.count();

    for (let i = 0; i < count; i++) {
      const row = rows.nth(i);
      const titleLink = row.locator("a").first();
      const title = (await titleLink.textContent())?.trim() ?? "";
      const detailUrl = (await titleLink.getAttribute("href")) ?? "";
      if (!title) continue;

      const locationMatch = title.match(/\[([A-Z]{2})\]/);
      const location = locationMatch ? locationMatch[1] : "";

      // Score: count filled signal bars
      const score = await row.locator('[class*="bar"][class*="active"], [class*="signal"] svg').count() || 4;

      const cells = row.locator("td");
      const postedDate = (await cells.nth(1).textContent())?.trim() ?? "";
      const dueDate = (await cells.nth(2).textContent())?.trim() ?? "";
      const description = (await row.locator("p, [class*='description']").first().textContent())?.trim() ?? "";

      const externalId = detailUrl.split("/").pop() ?? `jw-${Date.now()}-${i}`;

      all.push({ externalId, title, location, postedDate, dueDate, score, description, detailUrl, tab });
    }
  }

  return all;
}
```

- [ ] **Step 3: Run scraper in debug mode and fix selectors**

Use Playwright codegen to capture real selectors:

```bash
npx playwright codegen https://app.justwin.ai/leads
```

Update selectors in `scrape-leads.ts` to match actual DOM.

- [ ] **Step 4: Commit**

```bash
git add scripts/justwin-sync/scrape-leads.ts scripts/justwin-sync/types.ts
git commit -m "feat: scrape JustWin leads across Hot/Warm/Review tabs"
```

---

### Task 5: Download PDFs from detail pages

**Files:**
- Create: `rfp-dashboard/scripts/justwin-sync/scrape-detail.ts`
- Create: `rfp-dashboard/scripts/justwin-sync/download-pdfs.ts`

- [ ] **Step 1: Find PDF links on detail page**

```typescript
import type { Page } from "playwright";

export async function findPdfUrls(page: Page, detailUrl: string): Promise<string[]> {
  const base = process.env.JUSTWIN_BASE_URL ?? "https://app.justwin.ai";
  const url = detailUrl.startsWith("http") ? detailUrl : `${base}${detailUrl}`;
  await page.goto(url, { waitUntil: "networkidle" });

  const links = await page.locator('a[href*=".pdf"], a[download], a:has-text("PDF"), a:has-text("Download")').all();
  const urls: string[] = [];
  for (const link of links) {
    const href = await link.getAttribute("href");
    if (href) urls.push(href.startsWith("http") ? href : `${base}${href}`);
  }
  return [...new Set(urls)];
}
```

- [ ] **Step 2: Download PDFs to storage**

```typescript
import fs from "fs";
import path from "path";
import type { APIRequestContext } from "playwright";

const PDF_ROOT = process.env.PDF_STORAGE_PATH ?? "./storage/pdfs";

export async function downloadPdfs(
  request: APIRequestContext,
  externalId: string,
  pdfUrls: string[]
): Promise<string | undefined> {
  if (pdfUrls.length === 0) return undefined;

  const dir = path.join(PDF_ROOT, externalId);
  fs.mkdirSync(dir, { recursive: true });

  const target = path.join(dir, "rfp.pdf");
  const response = await request.get(pdfUrls[0]);
  if (!response.ok()) return undefined;

  fs.writeFileSync(target, await response.body());
  return target;
}
```

- [ ] **Step 3: Commit**

```bash
git add scripts/justwin-sync/scrape-detail.ts scripts/justwin-sync/download-pdfs.ts
git commit -m "feat: download RFP PDFs from JustWin detail pages"
```

---

### Task 6: Sync orchestrator CLI

**Files:**
- Create: `rfp-dashboard/scripts/justwin-sync/index.ts`
- Create: `rfp-dashboard/src/lib/justwin-mapper.ts`

- [ ] **Step 1: Create mapper `src/lib/justwin-mapper.ts`**

```typescript
import type { JustWinLead } from "../../scripts/justwin-sync/types";
import type { RfpRecord } from "@/types/rfp";

function parseJustWinDate(raw: string): string {
  // "Jun 12" → "2026-06-12" (assume current year)
  const d = new Date(`${raw} ${new Date().getFullYear()}`);
  return d.toISOString().split("T")[0];
}

export function mapLeadToRfp(lead: JustWinLead, pdfPath?: string): RfpRecord {
  const now = new Date().toISOString();
  return {
    id: `rfp-jw-${lead.externalId}`,
    externalId: lead.externalId,
    title: lead.title.replace(/\s*\[[A-Z]{2}\]\s*$/, "").trim(),
    client: lead.title.split(" for ").pop()?.replace(/\s*\[.*\]/, "").trim() ?? "",
    source: "justwin",
    sector: "Public Sector",
    location: lead.location,
    dueDate: parseJustWinDate(lead.dueDate),
    receivedDate: parseJustWinDate(lead.postedDate),
    stage: "intake",
    status: "new",
    priority: lead.score >= 4 ? "high" : "medium",
    fitScore: lead.score * 20,
    worthScore: null,
    goNoGo: null,
    assignedTo: null,
    estimatedValue: null,
    lastActivity: now,
    lastActivityNote: `Synced from JustWin (${lead.tab} leads)`,
    contractRole: "prime",
    description: lead.description,
    justwinTab: lead.tab,
    pdfPath,
    justwinDetailUrl: lead.detailUrl,
    syncedAt: now,
    pdfUrl: pdfPath ? `/api/rfps/rfp-jw-${lead.externalId}/pdf` : undefined,
  };
}
```

- [ ] **Step 2: Create `scripts/justwin-sync/index.ts`**

```typescript
import { getAuthenticatedContext } from "./browser";
import { scrapeAllLeads } from "./scrape-leads";
import { findPdfUrls } from "./scrape-detail";
import { downloadPdfs } from "./download-pdfs";
import { upsertRfp, getDb } from "../../src/lib/db";
import { mapLeadToRfp } from "../../src/lib/justwin-mapper";

async function main() {
  const jobId = process.argv[2] ?? "manual";
  console.log(`[justwin-sync] starting job ${jobId}`);

  const context = await getAuthenticatedContext();
  const page = await context.newPage();

  try {
    await page.goto(`${process.env.JUSTWIN_BASE_URL ?? "https://app.justwin.ai"}/leads`);
    const leads = await scrapeAllLeads(page);
    console.log(`[justwin-sync] found ${leads.length} leads`);

    let pdfs = 0;
    for (const lead of leads) {
      const pdfUrls = await findPdfUrls(page, lead.detailUrl);
      const pdfPath = await downloadPdfs(context.request, lead.externalId, pdfUrls);
      if (pdfPath) pdfs++;
      upsertRfp(mapLeadToRfp(lead, pdfPath));
    }

    console.log(JSON.stringify({ ok: true, jobId, rfpsFound: leads.length, pdfsDownloaded: pdfs }));
  } finally {
    await context.close();
    getDb().close();
  }
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, error: String(err) }));
  process.exit(1);
});
```

- [ ] **Step 3: Run full sync**

```bash
npm run sync:justwin
```

Expected: JSON output `{ ok: true, rfpsFound: N, pdfsDownloaded: M }`, rows in `data/rfps.db`, PDFs in `storage/pdfs/`.

- [ ] **Step 4: Commit**

```bash
git add scripts/justwin-sync/index.ts src/lib/justwin-mapper.ts
git commit -m "feat: JustWin sync orchestrator CLI"
```

---

### Task 7: API routes — trigger sync + serve PDFs

**Files:**
- Create: `rfp-dashboard/src/lib/justwin-sync-runner.ts`
- Create: `rfp-dashboard/src/app/api/justwin/sync/route.ts`
- Create: `rfp-dashboard/src/app/api/justwin/status/route.ts`
- Create: `rfp-dashboard/src/app/api/rfps/[id]/pdf/route.ts`

- [ ] **Step 1: Sync runner (spawn child process)**

```typescript
import { spawn } from "child_process";
import { randomUUID } from "crypto";
import { getDb } from "./db";

let running = false;

export function startJustWinSync(): { jobId: string } | { error: string } {
  if (running) return { error: "Sync already in progress" };

  const jobId = randomUUID();
  running = true;

  getDb().prepare(
    `INSERT INTO sync_jobs (id, status, started_at) VALUES (?, 'running', ?)`
  ).run(jobId, new Date().toISOString());

  const child = spawn("npm", ["run", "sync:justwin", "--", jobId], {
    cwd: process.cwd(),
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env },
  });

  let output = "";
  child.stdout?.on("data", (d) => { output += d; });
  child.stderr?.on("data", (d) => { output += d; });

  child.on("close", (code) => {
    running = false;
    const parsed = tryParseJson(output);
    getDb().prepare(`
      UPDATE sync_jobs SET status=?, finished_at=?, rfps_found=?, pdfs_downloaded=?, error=?
      WHERE id=?
    `).run(
      code === 0 ? "completed" : "failed",
      new Date().toISOString(),
      parsed?.rfpsFound ?? 0,
      parsed?.pdfsDownloaded ?? 0,
      code !== 0 ? (parsed?.error ?? output) : null,
      jobId
    );
  });

  return { jobId };
}

function tryParseJson(s: string) {
  try { return JSON.parse(s.trim().split("\n").pop() ?? ""); } catch { return null; }
}

export function getLatestSyncJob() {
  return getDb().prepare("SELECT * FROM sync_jobs ORDER BY started_at DESC LIMIT 1").get();
}
```

- [ ] **Step 2: `POST /api/justwin/sync`**

```typescript
import { NextResponse } from "next/server";
import { startJustWinSync } from "@/lib/justwin-sync-runner";

export async function POST() {
  const result = startJustWinSync();
  if ("error" in result) {
    return NextResponse.json(result, { status: 409 });
  }
  return NextResponse.json({ jobId: result.jobId, status: "running" });
}
```

- [ ] **Step 3: `GET /api/justwin/status`**

```typescript
import { NextResponse } from "next/server";
import { getLatestSyncJob } from "@/lib/justwin-sync-runner";

export async function GET() {
  return NextResponse.json(getLatestSyncJob() ?? { status: "idle" });
}
```

- [ ] **Step 4: `GET /api/rfps/[id]/pdf`**

```typescript
import { NextResponse } from "next/server";
import fs from "fs";
import { getDb } from "@/lib/db";

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const row = getDb().prepare("SELECT pdf_path FROM rfps WHERE id = ?").get(id) as { pdf_path?: string } | undefined;
  if (!row?.pdf_path || !fs.existsSync(row.pdf_path)) {
    return NextResponse.json({ error: "PDF not found" }, { status: 404 });
  }
  const buf = fs.readFileSync(row.pdf_path);
  return new NextResponse(buf, {
    headers: { "Content-Type": "application/pdf", "Content-Disposition": "inline" },
  });
}
```

- [ ] **Step 5: Commit**

```bash
git add src/lib/justwin-sync-runner.ts src/app/api/justwin/ src/app/api/rfps/
git commit -m "feat: API routes for JustWin sync trigger, status, and PDF serving"
```

---

### Task 8: Wire Sync button in dashboard UI

**Files:**
- Create: `rfp-dashboard/src/components/SyncJustWinButton.tsx`
- Modify: `rfp-dashboard/src/components/DashboardHeader.tsx`
- Modify: `rfp-dashboard/src/components/TopBar.tsx`
- Modify: `rfp-dashboard/src/lib/rfp-service.ts`

- [ ] **Step 1: Create `SyncJustWinButton.tsx`**

```tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { IconSync } from "./ui/icons";

export function SyncJustWinButton({ className = "" }: { className?: string }) {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [lastSynced, setLastSynced] = useState<string | null>(null);

  const pollStatus = useCallback(async () => {
    const res = await fetch("/api/justwin/status");
    const job = await res.json();
    if (job.status === "running") {
      setStatus("running");
      setTimeout(pollStatus, 2000);
    } else if (job.status === "completed") {
      setStatus("done");
      setLastSynced(job.finished_at);
      window.location.reload();
    } else if (job.status === "failed") {
      setStatus("error");
    }
  }, []);

  async function handleSync() {
    setStatus("running");
    const res = await fetch("/api/justwin/sync", { method: "POST" });
    if (!res.ok) {
      setStatus("error");
      return;
    }
    pollStatus();
  }

  useEffect(() => {
    fetch("/api/justwin/status")
      .then((r) => r.json())
      .then((job) => {
        if (job.finished_at) setLastSynced(job.finished_at);
        if (job.status === "running") pollStatus();
      });
  }, [pollStatus]);

  const label = status === "running" ? "Syncing…" : status === "error" ? "Sync Failed" : "Sync JustWin";

  return (
    <div className="flex flex-col items-end gap-3">
      <button
        type="button"
        onClick={handleSync}
        disabled={status === "running"}
        className={`sync-btn group flex items-center gap-2.5 rounded-xl border-2 border-zo-black bg-zo-black px-6 py-3 text-xs font-bold uppercase tracking-wider text-zo-white transition-smooth hover:border-zo-orange hover:bg-zo-orange disabled:opacity-60 ${className}`}
      >
        <IconSync className={`sync-icon h-4 w-4 ${status === "running" ? "animate-spin" : ""}`} />
        {label}
      </button>
      {lastSynced && (
        <p className="text-xs text-zo-text-muted">
          Last synced · {new Date(lastSynced).toLocaleString()}
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Replace static button in `DashboardHeader.tsx`**

Import and render `<SyncJustWinButton />` instead of the hardcoded button block.

- [ ] **Step 3: Update `getRfps()` in `rfp-service.ts`**

```typescript
import { getAllRfps } from "@/lib/db";

export async function getRfps(): Promise<RfpRecord[]> {
  try {
    const synced = getAllRfps();
    if (synced.length > 0) return synced;
  } catch {
    // DB not initialized yet
  }
  // existing API + mock fallback...
  return mockRfps;
}
```

- [ ] **Step 4: Manual E2E test**

1. `npm run dev`
2. Open dashboard → click "Sync JustWin"
3. Button shows "Syncing…" → page reloads with real JustWin data
4. Click an RFP PDF link → PDF opens

- [ ] **Step 5: Commit**

```bash
git add src/components/SyncJustWinButton.tsx src/components/DashboardHeader.tsx src/components/TopBar.tsx src/lib/rfp-service.ts
git commit -m "feat: wire Sync JustWin button to Playwright sync pipeline"
```

---

### Task 9: Optional scheduled sync (cron)

**Files:**
- Create: `rfp-dashboard/scripts/cron-sync.sh`

- [ ] **Step 1: Add cron script**

```bash
#!/bin/bash
cd "$(dirname "$0")/.."
npm run sync:justwin >> logs/justwin-sync.log 2>&1
```

- [ ] **Step 2: Document in README**

Add section: run every 6 hours via system cron or GitHub Actions scheduled workflow.

---

## Data Flow Diagram

```
User clicks "Sync JustWin"
        │
        ▼
POST /api/justwin/sync
        │
        ▼
spawn npm run sync:justwin
        │
        ├── Playwright → app.justwin.ai/leads
        │     ├── Hot Leads tab → scrape rows
        │     ├── Warm Leads tab → scrape rows
        │     └── Review tab → scrape rows
        │
        ├── For each lead → detail page → find PDF URL
        │
        ├── Download PDF → storage/pdfs/{id}/rfp.pdf
        │
        └── upsertRfp() → data/rfps.db
                │
                ▼
        Dashboard reads getAllRfps() → RfpTable
        PDF link → GET /api/rfps/{id}/pdf
```

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| JustWin UI changes break selectors | Use `data-testid` if available; add selector config file; run sync in CI weekly |
| Login MFA / CAPTCHA | Save session cookies; re-auth manually when expired |
| Large PDF count slows sync | Run sync in background; show progress; skip already-downloaded PDFs |
| Playwright on production (Vercel) | Deploy dashboard to Node server (Railway/Fly/Docker), not serverless |
| Terms of service | Confirm with JustWin that automation is permitted; prefer official API if offered |

---

## Verification Checklist

- [ ] `npm run sync:justwin` completes without error
- [ ] `data/rfps.db` contains rows matching JustWin Hot/Warm/Review leads
- [ ] PDFs exist in `storage/pdfs/` for leads that have documents
- [ ] Dashboard shows synced RFPs (not mock data) after sync
- [ ] "Sync JustWin" button shows running state and reloads on completion
- [ ] `/api/rfps/{id}/pdf` returns valid PDF bytes

---

## Future Enhancements (out of scope for v1)

1. Swap Playwright for JustWin REST API when/if available (`JUSTWIN_API_KEY` path already stubbed)
2. Push PDFs to Google Drive / CGN brain via existing MCP tools
3. Auto-trigger Stage 1 Go/No-Go analysis on new intake
4. Webhook from JustWin for real-time intake instead of polling
