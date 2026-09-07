import { chromium, type Browser, type BrowserContext, type Page } from "playwright";
import fs from "fs";
import path from "path";

const BASE_URL = process.env.JUSTWIN_BASE_URL ?? "https://app.justwin.ai";
const SESSION_PATH =
  process.env.JUSTWIN_SESSION_PATH ??
  path.join(process.cwd(), "data", "justwin-session.json");

export interface AuthContext {
  browser: Browser;
  context: BrowserContext;
}

export function invalidateSession(): boolean {
  if (!fs.existsSync(SESSION_PATH)) return false;
  try {
    fs.unlinkSync(SESSION_PATH);
    console.log(`[justwin-sync] cleared stale session at ${SESSION_PATH}`);
    return true;
  } catch (err) {
    console.warn(`[justwin-sync] could not clear session:`, err);
    return false;
  }
}

async function isLoginPage(page: Page): Promise<boolean> {
  const url = page.url();
  if (url.includes("/login") || url.includes("/sign")) {
    return true;
  }

  const emailInputs = await page
    .locator('input[type="email"], input[name="email"]')
    .count();
  const passwordInputs = await page.locator('input[type="password"]').count();
  return emailInputs > 0 && passwordInputs > 0;
}

async function performLogin(page: Page): Promise<void> {
  const email = process.env.JUSTWIN_EMAIL;
  const password = process.env.JUSTWIN_PASSWORD;
  if (!email || !password) {
    throw new Error(
      "JUSTWIN_EMAIL and JUSTWIN_PASSWORD are required for first login"
    );
  }

  await page
    .locator('input[type="email"], input[name="email"]')
    .first()
    .fill(email);
  await page.locator('input[type="password"]').first().fill(password);

  const loginButton = page.getByRole("button", { name: /^log in$/i });
  if ((await loginButton.count()) > 0) {
    await loginButton.first().click();
  } else {
    await page.locator('button[type="submit"]').first().click();
  }

  await page.waitForURL((url) => !url.pathname.includes("/login"), {
    timeout: 60000,
  });
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(2000);
}

async function openLeads(context: BrowserContext): Promise<Page> {
  const page = await context.newPage();
  await page.goto(`${BASE_URL}/leads`, {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  await page.waitForTimeout(2500);
  return page;
}

export async function getAuthenticatedContext(
  options: { forceFresh?: boolean } = {}
): Promise<AuthContext> {
  const forceFresh = Boolean(options.forceFresh);
  if (forceFresh) invalidateSession();

  const browser = await chromium.launch({
    headless: process.env.HEADLESS !== "false",
  });

  let usedSavedSession = fs.existsSync(SESSION_PATH) && !forceFresh;
  let context: BrowserContext;
  if (usedSavedSession) {
    const storage = JSON.parse(fs.readFileSync(SESSION_PATH, "utf-8"));
    context = await browser.newContext({ storageState: storage });
  } else {
    context = await browser.newContext();
  }

  let page = await openLeads(context);

  if (await isLoginPage(page)) {
    if (usedSavedSession) {
      console.warn(
        "[justwin-sync] saved session expired — logging in again with credentials"
      );
      await page.close();
      await context.close();
      invalidateSession();
      context = await browser.newContext();
      page = await openLeads(context);
    }
    await performLogin(page);
    fs.mkdirSync(path.dirname(SESSION_PATH), { recursive: true });
    await context.storageState({ path: SESSION_PATH });
  }

  if ((await isLoginPage(page)) || page.url().includes("/login")) {
    await browser.close();
    throw new Error(
      "JustWin login failed — still on login page. Check JUSTWIN_EMAIL / JUSTWIN_PASSWORD."
    );
  }

  await page.close();
  return { browser, context };
}

export async function ensureAuthenticatedPage(
  auth: AuthContext,
  page: Page
): Promise<{ auth: AuthContext; page: Page }> {
  await page.waitForTimeout(1500);
  if (!(await isLoginPage(page)) && !page.url().includes("/login")) {
    return { auth, page };
  }

  console.warn(
    "[justwin-sync] session lost after open — re-authenticating automatically"
  );
  try {
    await page.close();
  } catch {
    /* ignore */
  }
  try {
    await auth.context.close();
  } catch {
    /* ignore */
  }
  try {
    await auth.browser.close();
  } catch {
    /* ignore */
  }

  const fresh = await getAuthenticatedContext({ forceFresh: true });
  const next = await openLeads(fresh.context);
  if ((await isLoginPage(next)) || next.url().includes("/login")) {
    throw new Error(
      "JustWin login failed after auto re-auth. Check JUSTWIN_EMAIL / JUSTWIN_PASSWORD."
    );
  }
  return { auth: fresh, page: next };
}

export function getJustWinBaseUrl(): string {
  return BASE_URL;
}
