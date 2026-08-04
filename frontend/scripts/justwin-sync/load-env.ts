import fs from "fs";
import path from "path";
import { config } from "dotenv";

/**
 * Load .env files for CLI scripts (tsx does not auto-load like Next.js).
 */
export function loadEnv(): void {
  const root = process.cwd();
  // Highest precedence first. `override` stays false so a real environment
  // variable passed by the caller (e.g. HEADLESS=true) always wins over a
  // file, and .env.local wins over .env.
  const files = [".env.local", ".env"];

  for (const file of files) {
    const filePath = path.join(root, file);
    if (fs.existsSync(filePath)) {
      config({ path: filePath, override: false });
    }
  }
}

loadEnv();
