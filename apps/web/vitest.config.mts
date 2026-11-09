// Unit tests for the web UI (pure functions + server-rendered markup of components). Run: npm run test:unit
import path from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": path.resolve(import.meta.dirname, ".") } },
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    include: ["tests/unit/**/*.test.{ts,tsx}"],
    environment: "node",
    pool: "threads",
    maxWorkers: 2,
  },
});
