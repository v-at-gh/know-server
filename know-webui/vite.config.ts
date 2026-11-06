import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const ADMIN_TARGET = process.env.VITE_ADMIN_TARGET || "http://127.0.0.1:8000";
const ORCH_TARGET  = process.env.VITE_ORCH_TARGET  || "http://127.0.0.1:8002";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 8003,
    strictPort: true,
    proxy: {
      "/admin-api": {
        target: ADMIN_TARGET,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/admin-api/, "/api"),
      },
      "/orch-api": {
        target: ORCH_TARGET,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/orch-api/, "/api"),
      },
    },
  },
});
