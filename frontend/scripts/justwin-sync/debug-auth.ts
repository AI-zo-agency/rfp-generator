import "./load-env";
import { getAuthenticatedContext } from "./browser";

async function main() {
  const { browser, context } = await getAuthenticatedContext();
  const page = await context.newPage();
  await page.goto("https://app.justwin.ai/leads/c8833e28-bfbb-4bc3-8e04-6b2b73a82f10/summary");
  await page.waitForTimeout(5000);

  const storage = await page.evaluate(() => {
    const keys = Object.keys(localStorage);
    return keys.map((k) => ({ key: k, value: localStorage.getItem(k)?.slice(0, 80) }));
  });

  const cookies = await context.cookies();
  console.log(JSON.stringify({ storage, cookies: cookies.map((c) => ({ name: c.name, domain: c.domain })) }, null, 2));
  await browser.close();
}

main();
