// Builds the embed-host harness from a copy placed at `web/.e2e-embed-host/`,
// so `web`'s dependencies and Tailwind sources resolve exactly as for the
// standalone SPA build.
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const harnessDir = __dirname;
const webDir = path.resolve(harnessDir, "..");

export default defineConfig({
  root: webDir,
  base: "/embed-host/",
  publicDir: false,
  logLevel: "warn",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(webDir, "src"),
    },
  },
  build: {
    outDir: path.resolve(harnessDir, "dist"),
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 5000,
    rollupOptions: {
      input: path.resolve(harnessDir, "index.html"),
    },
  },
});
