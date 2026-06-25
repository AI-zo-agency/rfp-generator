import { chromium, type Browser, type BrowserContext } from "playwright";
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

async function isLoginPage(page: import("playwright").Page): Promise<boolean> {
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

async function performLogin(page: import("playwright").Page): Promise<void> {
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

export async function getAuthenticatedContext(): Promise<AuthContext> {
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
  await page.goto(`${BASE_URL}/leads`, {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });

  if (await isLoginPage(page)) {
    await performLogin(page);
    fs.mkdirSync(path.dirname(SESSION_PATH), { recursive: true });
    await context.storageState({ path: SESSION_PATH });
  }

  if (page.url().includes("/login")) {
    await browser.close();
    throw new Error("JustWin login failed — still on login page");
  }

  await page.close();
  return { browser, context };
}

export function getJustWinBaseUrl(): string {
  return BASE_URL;
}
