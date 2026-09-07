/**
 * ponytail: assert fin-motion reduced-motion helper stays callable.
 * Run: npx tsx src/financial/lib/fin-motion.selfcheck.ts
 */
import assert from "node:assert/strict";
import { prefersReducedMotion } from "./fin-motion";

assert.equal(typeof prefersReducedMotion(), "boolean");
console.log("fin-motion.selfcheck: ok");
