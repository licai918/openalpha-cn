import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/openapi.json": "http://127.0.0.1:8000"
    }
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
    coverage: {
      provider: "v8",
      enabled: true,
      reporter: ["text", "html"],
      // Floor set at (rounded down from) the coverage actually measured on
      // 2026-08-07 with the two existing App.test.tsx cases: statements
      // 68.59%, branches 60.67%, functions 64.86%, lines 70.08%. This gate
      // can only ratchet up as tests are added — it must never be lowered
      // to make a red run pass.
      thresholds: {
        statements: 68,
        branches: 60,
        functions: 64,
        lines: 70
      }
    }
  }
});
