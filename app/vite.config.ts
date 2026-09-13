import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Fixed dev-server port so the Tauri shell can reach it without discovery.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 1420,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
