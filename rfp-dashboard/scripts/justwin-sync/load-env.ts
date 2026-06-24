import fs from "fs";
import path from "path";
import { config } from "dotenv";

/**
 * Load .env files for CLI scripts (tsx does not auto-load like Next.js).
 */
export function loadEnv(): void {
  const root = process.cwd();
  const files = [".env", ".env.local"];

  for (const file of files) {
    const filePath = path.join(root, file);
    if (fs.existsSync(filePath)) {
      config({ path: filePath, override: true });
    }
  }
}

loadEnv();
