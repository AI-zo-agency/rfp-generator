import "./load-env";
import { getAuthenticatedContext } from "./browser";

const URL =
  "https://app.justwin.ai/leads/c8833e28-bfbb-4bc3-8e04-6b2b73a82f10/summary";

async function main() {
  const { browser, context } = await getAuthenticatedContext();
  const page = await context.newPage();

  try {
    await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 90000 });
    await page.waitForTimeout(5000);

    const body = ((await page.textContent("body")) ?? "").replace(/\s+/g, " ");
    const headings = await page.locator("h1,h2,h3,h4,strong").evaluateAll((els) =>
      els.map((el) => (el.textContent ?? "").trim()).filter(Boolean)
    );

    const packageMatches = body.match(/solicitation package[\s\S]{0,400}/i);
    const mbMatches = body.match(/.{0,80}\d+\.\d+\s*MB.{0,80}/gi);

    console.log(
      JSON.stringify(
        {
          url: page.url(),
          title: await page.title(),
          headings: headings.slice(0, 30),
          packageSnippet: packageMatches?.[0] ?? null,
          mbMatches,
          hasViewOriginal: body.includes("View Original Solicitation"),
          hasJamesMadison: body.includes("James Madison University"),
        },
        null,
        2
      )
    );
  } finally {
    await context.close();
    await browser.close();
  }
}

main();
