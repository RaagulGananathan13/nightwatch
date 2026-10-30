import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` proxies /api and /config.json to the deployed dashboard, so the UI can be
// developed locally against real data: set NIGHTWATCH_URL to the dashboard's https URL.
const deployed = process.env.NIGHTWATCH_URL;

export default defineConfig({
  plugins: [react()],
  build: { sourcemap: false, chunkSizeWarningLimit: 600 },
  server: deployed
    ? { proxy: { "/api": deployed, "/config.json": deployed } }
    : undefined,
});
