import "./load-env";
import { getAuthenticatedContext, getJustWinBaseUrl } from "./browser";
import { scrapeAllLeads } from "./scrape-leads";
import { downloadPdfFromSolicitationPackage } from "./solicitation-package";
import { closeDb, finishSyncJob, upsertRfp } from "../../src/lib/db";
import { mapLeadToRfp } from "../../src/lib/justwin-mapper";

/**
 * JustWin Playwright CLI — disabled while the dashboard uses FastAPI backend.
 * Set to true to run manually (also update package.json sync:justwin script).
 */
const JUSTWIN_SYNC_CLI_ENABLED = false;

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
  console.log(`[justwin-sync] starting job ${jobId}`);

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

    const leads = await scrapeAllLeads(page);
    console.log(`[justwin-sync] syncing ${leads.length} lead(s) only`);

    let pdfsDownloaded = 0;
    for (const lead of leads) {
      const pdfPath = await downloadPdfFromSolicitationPackage(
        page,
        context,
        lead.externalId,
        lead.detailUrl
      );
      if (pdfPath) pdfsDownloaded++;
      upsertRfp(mapLeadToRfp(lead, pdfPath));
    }

    if (jobId !== "manual") {
      finishSyncJob(jobId, {
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
      })
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (jobId !== "manual") {
      finishSyncJob(jobId, {
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
    closeDb();
  }
}

main();
