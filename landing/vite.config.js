import { defineConfig } from "vite";

// Relative base so the built site works from any path (sub-folder hosting,
// static buckets) without rewriting asset URLs.
export default defineConfig({
  base: "./",
  build: { target: "es2020", chunkSizeWarningLimit: 900 },
  server: { port: 5180, strictPort: true },
  preview: { port: 5181, strictPort: true },
});
