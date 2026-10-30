import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// `npm run dev` proxies /api and /config.json to the deployed dashboard, so the UI can be
// developed locally against real data: set NIGHTWATCH_URL to the dashboard's https URL.
const deployed = process.env.NIGHTWATCH_URL;

// The Content Security Policy for the deployed site: scripts, styles and API calls only from
// the site itself, plus calls to Cognito's login domain to exchange the sign-in code.
// Framing is blocked by CloudFront's X-Frame-Options header (a <meta> CSP can't do that).
export const CSP = [
  "default-src 'self'",
  "connect-src 'self' https://*.amazoncognito.com",
  "img-src 'self' data:",
  "style-src 'self'",
  "script-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self' https://*.amazoncognito.com",
].join("; ");

function contentSecurityPolicy(): Plugin {
  return {
    name: "nightwatch-csp",
    apply: "build", // the dev server needs inline scripts for hot reload
    transformIndexHtml: () => [
      { tag: "meta", attrs: { "http-equiv": "Content-Security-Policy", content: CSP }, injectTo: "head-prepend" },
    ],
  };
}

export default defineConfig({
  plugins: [react(), contentSecurityPolicy()],
  build: { sourcemap: false, chunkSizeWarningLimit: 600 },
  server: deployed ? { proxy: { "/api": deployed, "/config.json": deployed } } : undefined,
});
