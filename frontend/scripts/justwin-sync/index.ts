import "./load-env";
import { getAuthenticatedContext, getJustWinBaseUrl } from "./browser";
import { collectLeads } from "./scrape-leads";
import { createApiClient } from "./justwin-api";
import { downloadSolicitationPdf } from "./solicitation-package";
import { mapLeadToRfp } from "../../src/lib/justwin-mapper";
import {
  finishSyncJob,
  upsertRfpViaBackend,
} from "../../src/lib/sync-jobs-api";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8001";

/**
 * JustWin Playwright CLI — enabled for automated browser sync.
 */
const JUSTWIN_SYNC_CLI_ENABLED = true;

async function uploadPdfToBackend(rfpId: string, pdfPath: string): Promise<void> {
  const fs = await import("fs");
  const content = fs.readFileSync(pdfPath);
  const response = await fetch(`${BACKEND_URL}/api/v1/rfps/${rfpId}/pdf`, {
    method: "POST",
    headers: { "Content-Type": "application/pdf" },
    body: content,
  });
  if (!response.ok) {
    throw new Error(`PDF upload failed for ${rfpId}: ${response.status}`);
  }
}

async function extractDueDateFromPdf(pdfPath: string): Promise<string | null> {
  try {
    const fs = await import("fs");
    const content = fs.readFileSync(pdfPath);
    const response = await fetch(`${BACKEND_URL}/api/v1/rfps/extract-due-date`, {
      method: "POST",
      headers: { "Content-Type": "application/pdf" },
      body: content,
    });
    if (response.ok) {
      const data = (await response.json()) as { dueDate?: string | null };
      return data.dueDate ?? null;
    }
  } catch (err) {
    console.warn(`[justwin-sync] due date extraction skipped:`, err);
  }
  return null;
}

async function main() {
  if (!JUSTWIN_SYNC_CLI_ENABLED) {
    console.error(
      JSON.stringify({
        ok: false,
        error:
          "JustWin Playwright sync CLI is disabled. Set JUSTWIN_SYNC_CLI_ENABLED=true in scripts/justwin-sync/index.ts",
      })
    );
    process.exit(1);
  }

  const jobId = process.argv[2] ?? "manual";
  // "-" means the dashboard asked for every date; anything else is an ISO date.
  const rawDate = process.argv[3] ?? new Date().toISOString().slice(0, 10);
  const syncDate = rawDate === "-" ? "" : rawDate;
  const targetTab = process.argv[4] ?? "all";

  console.log(
    `[justwin-sync] starting job ${jobId} (date: ${syncDate || "any"}, tab: ${targetTab})`
  );

  const { browser, context } = await getAuthenticatedContext();
  const page = await context.newPage();

  try {
    await page.goto(`${getJustWinBaseUrl()}/leads`, {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });
    await page.waitForTimeout(3000);

    if (page.url().includes("/login")) {
      throw new Error("Not authenticated — delete data/justwin-session.json and rerun sync");
    }

    const client = await createApiClient(page);
    const leads = await collectLeads(client, syncDate, targetTab);
    console.log(`[justwin-sync] found ${leads.length} matching lead(s)`);

    let pdfsDownloaded = 0;
    for (const lead of leads) {
      let pdfPath: string | undefined;
      try {
        pdfPath = await downloadSolicitationPdf(client, lead.externalId);
      } catch (pdfErr) {
        console.warn(
          `[justwin-sync] PDF download warning for ${lead.externalId}:`,
          pdfErr instanceof Error ? pdfErr.message : pdfErr
        );
      }

      // JustWin supplies the due date directly; only fall back to parsing the
      // PDF when the lead has none.
      if (pdfPath && !lead.dueDate) {
        const extractedDueDate = await extractDueDateFromPdf(pdfPath);
        if (extractedDueDate) {
          lead.dueDate = extractedDueDate;
        }
      }

      const record = mapLeadToRfp(lead, pdfPath ? `pending:${lead.externalId}` : undefined);
      await upsertRfpViaBackend(record);

      if (pdfPath) {
        await uploadPdfToBackend(record.id, pdfPath);
        pdfsDownloaded++;
      }
    }

    if (jobId !== "manual") {
      await finishSyncJob(jobId, {
        status: "completed",
        rfpsFound: leads.length,
        pdfsDownloaded,
      });
    }

    console.log(
      JSON.stringify({
        ok: true,
        jobId,
        rfpsFound: leads.length,
        pdfsDownloaded,
        syncDate,
        targetTab,
      })
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (jobId !== "manual") {
      await finishSyncJob(jobId, {
        status: "failed",
        rfpsFound: 0,
        pdfsDownloaded: 0,
        error: message,
      });
    }
    console.error(JSON.stringify({ ok: false, jobId, error: message }));
    process.exitCode = 1;
  } finally {
    await context.close();
    await browser.close();
  }
}

main();
