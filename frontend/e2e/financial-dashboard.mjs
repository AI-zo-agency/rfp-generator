/**
 * Adversarial Playwright UI pass for /financial-insights.
 * Goal: find crashes, blank panels, bad URL handling, API error fallout.
 *
 * Requires: frontend on :3001, backend on :8001 (or NEXT_PUBLIC_BACKEND_URL).
 * Run: node e2e/financial-dashboard.mjs
 */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = process.env.FINANCIAL_UI_URL || "http://localhost:3001";
const E2E_EMAIL = process.env.E2E_EMAIL || "";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "";
const HERE = dirname(fileURLToPath(import.meta.url));
const ARTIFACTS = join(HERE, "artifacts");

const TABS = [
  { id: "agency", label: /Agency/i },
  { id: "quickbooks", label: /QuickBooks/i },
  { id: "teamwork", label: /Teamwork/i },
  { id: "iworker", label: /iWorker/i },
  { id: "sources", label: /Data Sources/i },
];

const failures = [];
const soft = [];

function fail(name, detail) {
  failures.push({ name, detail: String(detail) });
  console.error(`FAIL  ${name}: ${detail}`);
}

function softFail(name, detail) {
  soft.push({ name, detail: String(detail) });
  console.warn(`SOFT  ${name}: ${detail}`);
}

function ok(name) {
  console.log(`PASS  ${name}`);
}

async function shot(page, name) {
  mkdirSync(ARTIFACTS, { recursive: true });
  await page.screenshot({ path: join(ARTIFACTS, `${name}.png`), fullPage: true });
}

async function collectPageErrors(page, bag) {
  page.on("pageerror", (err) => bag.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") bag.push(`console.error: ${msg.text()}`);
  });
}

async function goto(page, path) {
  const res = await page.goto(`${FRONTEND}${path}`, { waitUntil: "domcontentloaded", timeout: 45_000 });
  return res;
}

async function waitForDashboard(page, timeout = 20_000) {
  await page
    .getByText("Financial Workspace")
    .or(page.locator('[role="tablist"]'))
    .first()
    .waitFor({ state: "visible", timeout });
}

async function loginViaForm(page, email, password) {
  await page.goto(`${FRONTEND}/login`, { waitUntil: "domcontentloaded", timeout: 45_000 });
  await page.locator('input[name="login-email"]').fill(email);
  await page.locator('input[name="login-password"]').fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

async function run() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const pageErrors = [];
  await collectPageErrors(page, pageErrors);

  // 0a. Wrong password stays on /login with an error
  {
    const name = "login-rejects-wrong-password";
    try {
      await loginViaForm(page, E2E_EMAIL || "nobody@example.com", "definitely-wrong");
      await page.waitForTimeout(2500);
      const url = page.url();
      const err = await page.locator(".text-red-700, [class*='red']").first().textContent().catch(() => "");
      if (/\/financial-insights|\/choose/.test(url)) {
        fail(name, `wrong password reached ${url}`);
      } else if (!/login/.test(url)) {
        softFail(name, `unexpected url ${url}`);
      } else {
        ok(name + (err ? ` (${err.trim().slice(0, 80)})` : ""));
      }
    } catch (e) {
      await shot(page, name);
      fail(name, e);
    }
  }

  // 0b. Empty submit is blocked by HTML required
  {
    const name = "login-empty-fields-blocked";
    try {
      await page.goto(`${FRONTEND}/login`, { waitUntil: "domcontentloaded" });
      await page.getByRole("button", { name: /log in/i }).click();
      assert.match(page.url(), /login/);
      ok(name);
    } catch (e) {
      await shot(page, name);
      fail(name, e);
    }
  }

  // 0c. Real credentials → choose → financial dashboard
  if (E2E_EMAIL && E2E_PASSWORD) {
    const name = "login-real-credentials-opens-dashboard";
    try {
      await loginViaForm(page, E2E_EMAIL, E2E_PASSWORD);
      await page.waitForURL(/\/(choose|financial-insights)/, { timeout: 20_000 });
      if (/\/choose/.test(page.url())) {
        await page.getByRole("link", { name: /Financial Dashboard/i }).click();
      }
      await waitForDashboard(page, 25_000);
      ok(name);
    } catch (e) {
      await shot(page, name);
      fail(name, e);
    }
  } else {
    await context.addInitScript(() => {
      localStorage.setItem("auth_token", "e2e-adversarial-token");
      localStorage.setItem("auth_user", JSON.stringify({ email: "e2e@zo.test" }));
    });
    softFail("login-real-credentials-opens-dashboard", "E2E_EMAIL/E2E_PASSWORD not set; using stub token");
  }

  // 0. Unauthenticated visit must leave the spinner and hit login
  {
    const name = "unauthenticated-redirects-to-login";
    const anon = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const anonPage = await anon.newPage();
    try {
      await anonPage.goto(`${FRONTEND}/financial-insights`, {
        waitUntil: "domcontentloaded",
        timeout: 45_000,
      });
      await anonPage.waitForURL(/\/login/, { timeout: 8_000 });
      ok(name);
    } catch (e) {
      const url = anonPage.url();
      const body = await anonPage.locator("body").innerText().catch(() => "");
      await shot(anonPage, name);
      fail(name, `${e} url=${url} body=${body.slice(0, 180)}`);
    } finally {
      await anon.close();
    }
  }

  // 1. Default load
  {
    const name = "loads-financial-insights";
    try {
      const res = await goto(page, "/financial-insights");
      assert.equal(res?.ok(), true, `HTTP ${res?.status()}`);
      await waitForDashboard(page);
      ok(name);
    } catch (e) {
      await shot(page, name);
      fail(name, e);
    }
  }

  // 2. Unknown tab must not crash (falls back to quickbooks)
  {
    const name = "unknown-tab-query-does-not-crash";
    const before = pageErrors.length;
    try {
      await goto(page, "/financial-insights?tab=not-a-real-tab&view=garbage");
      await waitForDashboard(page);
      const selected = await page.locator('[role="tab"][aria-selected="true"]').first().textContent();
      if (!/QuickBooks|Agency|Teamwork|iWorker|AI|Sources/i.test(selected || "")) {
        softFail(name, `unexpected selected tab text: ${selected}`);
      } else {
        ok(name);
      }
      if (pageErrors.length > before) {
        softFail(name + "-errors", pageErrors.slice(before).join(" | "));
      }
    } catch (e) {
      await shot(page, name);
      fail(name, e);
    }
  }

  // 3. Click every tab; panel region must appear; no uncaught pageerrors
  for (const tab of TABS) {
    const name = `tab-switch-${tab.id}`;
    const before = pageErrors.length;
    try {
      await goto(page, `/financial-insights?tab=${tab.id}`);
      await waitForDashboard(page);
      const tabBtn = page.locator(`[role="tab"][aria-controls="financial-panel-${tab.id}"]`);
      await tabBtn.click({ timeout: 10_000 });
      await page.waitForTimeout(800);
      const selected = await tabBtn.getAttribute("aria-selected");
      assert.equal(selected, "true", "tab not selected after click");
      const panel = page.locator(`#financial-panel-${tab.id}`);
      // Panel may be conditionally rendered; if missing, that's a UI bug.
      const panelCount = await panel.count();
      if (panelCount === 0) {
        softFail(name, `missing #financial-panel-${tab.id}`);
      } else {
        ok(name);
      }
      if (pageErrors.length > before) {
        softFail(`${name}-pageerrors`, pageErrors.slice(before).join(" | "));
      }
    } catch (e) {
      await shot(page, name);
      fail(name, e);
    }
  }

  // 4. Agency deep views
  for (const view of ["jobs", "mapping", "control"]) {
    const name = `agency-view-${view}`;
    try {
      await goto(page, `/financial-insights?tab=agency&view=${view}`);
      await waitForDashboard(page);
      await page.waitForTimeout(1500);
      const body = await page.locator("body").innerText();
      if (/Something went wrong|Application error|Unhandled/i.test(body)) {
        fail(name, "error boundary text on page");
        await shot(page, name);
      } else {
        ok(name);
      }
    } catch (e) {
      await shot(page, name);
      fail(name, e);
    }
  }

  // 5. API 500 on agency overview → UI must not white-screen
  {
    const name = "agency-survives-overview-500";
    try {
      await context.route("**/api/v1/financials/agency/overview**", async (route) => {
        await route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' });
      });
      await goto(page, "/financial-insights?tab=agency");
      await page.waitForTimeout(2000);
      const crashed = await page.locator("body").innerText();
      if (/Application error|Unhandled Runtime Error/i.test(crashed)) {
        fail(name, "React crashed on 500 overview");
        await shot(page, name);
      } else {
        ok(name);
      }
      await context.unroute("**/api/v1/financials/agency/overview**");
    } catch (e) {
      await shot(page, name);
      fail(name, e);
    }
  }

  // 6. API empty client-map → mapping view still usable
  {
    const name = "mapping-survives-empty-client-map";
    try {
      await context.route("**/api/v1/financials/client-map**", async (route) => {
        const url = route.request().url();
        if (url.includes("unmatched")) {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ teamwork: [], quickbooks: [] }),
          });
          return;
        }
        if (url.includes("job-overrides")) {
          await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
          return;
        }
        if (route.request().method() === "GET" && !url.includes("ai-insights")) {
          await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
          return;
        }
        await route.continue();
      });
      await goto(page, "/financial-insights?tab=agency&view=mapping");
      await page.waitForTimeout(2000);
      const text = await page.locator("body").innerText();
      if (/Application error/i.test(text)) {
        fail(name, "crashed on empty map");
        await shot(page, name);
      } else {
        ok(name);
      }
      await context.unroute("**/api/v1/financials/client-map**");
    } catch (e) {
      await shot(page, name);
      fail(name, e);
    }
  }

  // 7. Mobile nav open/close
  {
    const name = "mobile-nav-toggle";
    try {
      await page.setViewportSize({ width: 390, height: 844 });
      await goto(page, "/financial-insights?tab=quickbooks");
      await page.waitForSelector('[role="tablist"], button[aria-label*="financial" i]', { timeout: 15_000 });
      const openBtn = page.locator('button[aria-label*="financial sections" i], button[aria-label*="Open" i]').first();
      if ((await openBtn.count()) > 0) {
        await openBtn.click();
        await page.waitForTimeout(400);
      }
      ok(name);
    } catch (e) {
      await shot(page, name);
      softFail(name, e);
    } finally {
      await page.setViewportSize({ width: 1440, height: 900 });
    }
  }

  // 8. Rapid tab thrash (race / stale state)
  {
    const name = "rapid-tab-thrash";
    const before = pageErrors.length;
    try {
      await goto(page, "/financial-insights");
      await waitForDashboard(page);
      for (let i = 0; i < 12; i++) {
        const tab = TABS[i % TABS.length];
        await page.locator(`[role="tab"][aria-controls="financial-panel-${tab.id}"]`).click({ timeout: 5_000 });
      }
      await page.waitForTimeout(1000);
      if (pageErrors.length > before) {
        softFail(name, pageErrors.slice(before).join(" | "));
      } else {
        ok(name);
      }
    } catch (e) {
      await shot(page, name);
      fail(name, e);
    }
  }

  await browser.close();

  const report = { failures, soft, pageErrors };
  mkdirSync(ARTIFACTS, { recursive: true });
  writeFileSync(join(ARTIFACTS, "report.json"), JSON.stringify(report, null, 2));

  console.log("\n── summary ──");
  console.log(`hard failures: ${failures.length}`);
  console.log(`soft issues:   ${soft.length}`);
  console.log(`page errors:   ${pageErrors.length}`);
  if (failures.length) {
    process.exitCode = 1;
  }
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
