import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/static": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: path.resolve(__dirname, "../src/blender_vault/web/studio_dist"),
    emptyOutDir: true,
    sourcemap: false,
  },
});
