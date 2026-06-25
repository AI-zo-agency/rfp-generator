import "./load-env";
import { getAuthenticatedContext, getJustWinBaseUrl } from "./browser";

const TITLE = "Digital Advertising Services for James Madison University";

async function main() {
  const { browser, context } = await getAuthenticatedContext();
  const page = await context.newPage();

  try {
    await page.goto(`${getJustWinBaseUrl()}/leads`, {
      waitUntil: "domcontentloaded",
      timeout: 90000,
    });
    await page.waitForTimeout(2000);

    const archived = page.getByText(/^archived$/i);
    console.log("archived count:", await archived.count());
    await archived.first().click();
    await page.waitForTimeout(2000);

    const search = page.locator('input[placeholder*="Search solicitations" i]').first();
    await search.fill(TITLE);
    await page.waitForTimeout(3000);

    const matches = page.getByText(TITLE, { exact: false });
    console.log("match count:", await matches.count());

    for (let i = 0; i < Math.min(await matches.count(), 5); i++) {
      const text = ((await matches.nth(i).textContent()) ?? "").replace(/\s+/g, " ");
      console.log(`candidate ${i}:`, text.slice(0, 120));
    }

    const target = matches.filter({ hasText: TITLE }).first();
    await target.scrollIntoViewIfNeeded();
    await target.click({ timeout: 15000 });
    await page.waitForTimeout(3000);

    console.log(
      JSON.stringify({
        ok: true,
        url: page.url(),
        title: await page.title(),
      })
    );
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
