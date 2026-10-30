# Web dashboard

A React + Vite app for the on-call team. It is served from S3 through CloudFront, and you sign
in with Cognito ([ADR 0015](../docs/decisions/0015-dashboard.md)).

| Page | What it shows |
|---|---|
| Incidents | Open and resolved incidents, each with its likely cause and whether the guard verified it |
| Incident | The diagnosis with clickable `[E#]` evidence, and earlier incidents it looks like. Also the evidence viewer (every query and its raw result), fixes and approvals, the timeline, and the postmortem |
| Evaluation | Accuracy, time to diagnosis and evidence validity from `make eval`, with a chart |
| Runbooks | The fixes NightWatch may run, and how each one is checked and undone |
| Settings | The kill switch, the LLMs in use, and the watched services (read-only) |

## Develop

```bash
cd dashboard
npm ci
npm run typecheck && npm test
NIGHTWATCH_URL=https://<your-distribution>.cloudfront.net npm run dev
```

`npm run dev` sends `/api` and `/config.json` to the deployed dashboard, so you work against
real data. Signing in redirects to the deployed site's address, which is the only callback
Cognito allows.

The tests use Node's built-in runner (`node --test`) and need no extra packages. They cover the
PKCE sign-in, the token refresh, the markdown parser and the routes.

## Deploy

CI deploys on every merge to `main` that touches `dashboard/` or `eval/results.json`
(`.github/workflows/dashboard.yml`). To deploy by hand, run `make dashboard dashboard-deploy`.
