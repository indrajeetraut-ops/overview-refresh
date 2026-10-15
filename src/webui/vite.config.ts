import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// Talium dashboard front-end (MVP plan Track U). De-Figma'd: no Figma Make plugins.
// `vite build` -> dist/, which the FastAPI app serves via StaticFiles, mounted after /v1/*
// (server/api/app.py). Dev: `npm run dev` proxies /v1 -> the FastAPI dashboard API on :8090.
// The default data source is the in-repo mock, so no backend is required to run the prototype.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // VITE_API_TARGET lets a synthetic-org dev session (plan §5 P10b) point at a second API
      // process (e.g. localhost:8091, serving econ_synth) without editing this file.
      "/v1": { target: process.env.VITE_API_TARGET ?? "http://localhost:8090", changeOrigin: true },
    },
  },
});
