import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    port: 3000,
  },
  build: {
    rolldownOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (id.includes("/recharts/")) return "charts";
          if (/\/(react|react-dom|react-router|react-router-dom|zustand|@tanstack)\//.test(id)) {
            return "vendor";
          }
        },
      },
    },
  },
});
