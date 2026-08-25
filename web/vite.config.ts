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
      // Floor set at (rounded down from) the coverage actually measured. This gate
      // can only ratchet up as tests are added — it must never be lowered to make a
      // red run pass.
      //
      // 2026-08-07, two App.test.tsx cases: statements 68.59%, branches 60.67%,
      //   functions 64.86%, lines 70.08%  →  68 / 60 / 64 / 70.
      // 2026-08-25, V2-P5-019 (panel-state union + component tests for all four
      //   panels, 53 → 150 tests): statements 91.35%, branches 83.67%,
      //   functions 89.88%, lines 92.55%  →  91 / 83 / 89 / 92.
      thresholds: {
        statements: 91,
        branches: 83,
        functions: 89,
        lines: 92
      }
    }
  }
});
