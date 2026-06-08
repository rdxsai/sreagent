import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxy the API to the FastAPI backend so the SSE stream is same-origin.
// Build: emits to dist/, which FastAPI serves in production.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/demo": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
