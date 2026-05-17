import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const proxyTarget = process.env.VITE_PROXY_TARGET || "http://127.0.0.1:9004";
const apiProxy = {
  "/api": {
    target: proxyTarget,
    changeOrigin: true,
  },
  "/health": {
    target: proxyTarget,
    changeOrigin: true,
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: true,
    host: "0.0.0.0",
    port: 8084,
    proxy: apiProxy,
  },
  preview: {
    allowedHosts: true,
    host: "0.0.0.0",
    port: 8084,
    proxy: apiProxy,
  },
});
