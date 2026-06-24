import Database from "better-sqlite3";
import fs from "fs";
import path from "path";
import type { GoNoGoAnalysis, RfpRecord } from "@/types/rfp";

const DB_PATH =
  process.env.DATABASE_PATH ?? path.join(process.cwd(), "data", "rfps.db");

let db: Database.Database | null = null;

function ensureDbDir(): void {
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
}

export function getDb(): Database.Database {
  if (!db) {
    ensureDbDir();
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
    ensureRfpColumns(db);
  }
  return db;
}

function ensureRfpColumns(database: Database.Database): void {
  const columns = database
    .prepare("PRAGMA table_info(rfps)")
    .all() as Array<{ name: string }>;
  const names = new Set(columns.map((column) => column.name));
  if (!names.has("go_no_go_analysis")) {
    database.exec("ALTER TABLE rfps ADD COLUMN go_no_go_analysis TEXT");
  }
}

export function closeDb(): void {
  if (db) {
    db.close();
    db = null;
  }
}

function toDbParams(rfp: RfpRecord) {
  return {
    ...rfp,
    pageLimit: rfp.pageLimit ?? null,
    goNoGo: rfp.goNoGo ?? null,
    assignedTo: rfp.assignedTo ?? null,
    estimatedValue: rfp.estimatedValue ?? null,
    description: rfp.description ?? null,
    justwinTab: rfp.justwinTab ?? null,
    pdfPath: rfp.pdfPath ?? null,
    justwinDetailUrl: rfp.justwinDetailUrl ?? null,
    syncedAt: rfp.syncedAt ?? null,
    externalId: rfp.externalId ?? rfp.id,
  };
}

export function upsertRfp(rfp: RfpRecord): void {
  getDb()
    .prepare(
      `
    INSERT INTO rfps (
      id, external_id, title, client, source, sector, location,
      due_date, received_date, stage, status, priority, fit_score, worth_score,
      go_no_go, assigned_to, estimated_value, page_limit, last_activity,
      last_activity_note, contract_role, description, justwin_tab, pdf_path,
      justwin_detail_url, synced_at
    )
    VALUES (
      @id, @externalId, @title, @client, @source, @sector, @location,
      @dueDate, @receivedDate, @stage, @status, @priority, @fitScore, @worthScore,
      @goNoGo, @assignedTo, @estimatedValue, @pageLimit, @lastActivity,
      @lastActivityNote, @contractRole, @description, @justwinTab, @pdfPath,
      @justwinDetailUrl, @syncedAt
    )
    ON CONFLICT(external_id) DO UPDATE SET
      title = excluded.title,
      client = excluded.client,
      due_date = excluded.due_date,
      received_date = excluded.received_date,
      fit_score = excluded.fit_score,
      description = excluded.description,
      justwin_tab = excluded.justwin_tab,
      pdf_path = COALESCE(excluded.pdf_path, rfps.pdf_path),
      last_activity = excluded.last_activity,
      last_activity_note = excluded.last_activity_note,
      synced_at = excluded.synced_at
  `
    )
    .run(toDbParams(rfp));
}

export function getAllRfps(): RfpRecord[] {
  const rows = getDb()
    .prepare("SELECT * FROM rfps ORDER BY synced_at DESC, received_date DESC")
    .all() as Record<string, unknown>[];
  const rfps = rows.map(rowToRfp);

  const byTitle = new Map<string, RfpRecord>();
  for (const rfp of rfps) {
    const key = rfp.title.toLowerCase();
    const existing = byTitle.get(key);
    if (!existing) {
      byTitle.set(key, rfp);
      continue;
    }
    const preferCurrent =
      Boolean(rfp.pdfPath && !existing.pdfPath) ||
      (rfp.syncedAt ?? "") > (existing.syncedAt ?? "");
    if (preferCurrent) {
      byTitle.set(key, rfp);
    }
  }

  return [...byTitle.values()].sort(
    (a, b) =>
      new Date(b.receivedDate).getTime() - new Date(a.receivedDate).getTime()
  );
}

export function getRfpById(id: string): RfpRecord | null {
  const row = getDb()
    .prepare("SELECT * FROM rfps WHERE id = ? OR external_id = ?")
    .get(id, id) as Record<string, unknown> | undefined;
  return row ? rowToRfp(row) : null;
}

export function insertManualRfp(rfp: RfpRecord): void {
  getDb()
    .prepare(
      `
    INSERT INTO rfps (
      id, external_id, title, client, source, sector, location,
      due_date, received_date, stage, status, priority, fit_score, worth_score,
      go_no_go, assigned_to, estimated_value, page_limit, last_activity,
      last_activity_note, contract_role, description, justwin_tab, pdf_path,
      justwin_detail_url, synced_at
    )
    VALUES (
      @id, @externalId, @title, @client, @source, @sector, @location,
      @dueDate, @receivedDate, @stage, @status, @priority, @fitScore, @worthScore,
      @goNoGo, @assignedTo, @estimatedValue, @pageLimit, @lastActivity,
      @lastActivityNote, @contractRole, @description, @justwinTab, @pdfPath,
      @justwinDetailUrl, @syncedAt
    )
  `
    )
    .run(toDbParams(rfp));
}

export function updateRfpGoNoGo(
  id: string,
  goNoGo: RfpRecord["goNoGo"]
): boolean {
  const result = getDb()
    .prepare(
      `UPDATE rfps SET go_no_go = ?, last_activity = ?, last_activity_note = ?
       WHERE id = ? OR external_id = ?`
    )
    .run(
      goNoGo,
      new Date().toISOString(),
      goNoGo === "go" ? "Marked as Go — ready for proposal draft" : "Go/No-Go updated",
      id,
      id
    );
  return result.changes > 0;
}

export function getRfpPdfPath(id: string): string | null {
  const row = getDb()
    .prepare("SELECT pdf_path FROM rfps WHERE id = ?")
    .get(id) as { pdf_path?: string } | undefined;
  return row?.pdf_path ?? null;
}

function parseGoNoGoAnalysis(raw: unknown): GoNoGoAnalysis | null {
  if (typeof raw !== "string" || !raw.trim()) return null;
  try {
    return JSON.parse(raw) as GoNoGoAnalysis;
  } catch {
    return null;
  }
}

function rowToRfp(row: Record<string, unknown>): RfpRecord {
  const id = row.id as string;
  return {
    id,
    externalId: row.external_id as string,
    title: row.title as string,
    client: (row.client as string) ?? "",
    source: (row.source as RfpRecord["source"]) ?? "justwin",
    sector: (row.sector as string) ?? "Public Sector",
    location: (row.location as string) ?? "",
    dueDate: row.due_date as string,
    receivedDate: row.received_date as string,
    stage: row.stage as RfpRecord["stage"],
    status: row.status as RfpRecord["status"],
    priority: row.priority as RfpRecord["priority"],
    fitScore: (row.fit_score as number | null | undefined) ?? null,
    worthScore: (row.worth_score as number | null | undefined) ?? null,
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
    goNoGoAnalysis: parseGoNoGoAnalysis(row.go_no_go_analysis),
    pdfUrl: row.pdf_path ? `/api/rfps/${id}/pdf` : undefined,
  };
}

export interface SyncJobRow {
  id: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  rfps_found: number;
  pdfs_downloaded: number;
  error: string | null;
}

export function createSyncJob(id: string): void {
  getDb()
    .prepare(
      `INSERT INTO sync_jobs (id, status, started_at) VALUES (?, 'running', ?)`
    )
    .run(id, new Date().toISOString());
}

export function finishSyncJob(
  id: string,
  result: {
    status: "completed" | "failed";
    rfpsFound: number;
    pdfsDownloaded: number;
    error?: string;
  }
): void {
  getDb()
    .prepare(
      `
    UPDATE sync_jobs
    SET status = ?, finished_at = ?, rfps_found = ?, pdfs_downloaded = ?, error = ?
    WHERE id = ?
  `
    )
    .run(
      result.status,
      new Date().toISOString(),
      result.rfpsFound,
      result.pdfsDownloaded,
      result.error ?? null,
      id
    );
}

export function getLatestSyncJob(): SyncJobRow | null {
  const row = getDb()
    .prepare("SELECT * FROM sync_jobs ORDER BY started_at DESC LIMIT 1")
    .get();
  return (row as SyncJobRow | undefined) ?? null;
}

export function getRunningSyncJob(): SyncJobRow | null {
  const row = getDb()
    .prepare(
      "SELECT * FROM sync_jobs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
    )
    .get();
  return (row as SyncJobRow | undefined) ?? null;
}
