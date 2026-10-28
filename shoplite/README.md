# ShopLite demo app

A tiny serverless store API that NightWatch watches. It costs nothing to run: one Lambda behind a
Function URL, one DynamoDB table with provisioned capacity, and a canary that sends a little traffic
every minute so the alarms and metric baselines have real data.

| Route | What it does |
|---|---|
| `GET /health` | Returns `{"status": "ok", "version": "<git sha>"}` |
| `GET /products` | Lists the five seeded products |
| `POST /orders` | `{"product_id": "p-100", "quantity": 1}` places an order (quantity 1–10) |
| `GET /orders/{id}` | Reads an order back |

The Function URL uses **AWS_IAM** auth, so every request must be SigV4-signed. Unsigned traffic is
refused before the function runs. `shoplite.canary.signed_request` shows how to sign a request.

## Logs

Every request writes exactly one JSON line, for example:

```json
{"level":"ERROR","msg":"request","service":"shoplite","version":"3f9c1a2b7d4e","request_id":"…","method":"POST","route":"POST /orders","status":502,"latency_ms":41.7,"error_type":"InjectedFaultError"}
```

`version` is the deployed git SHA, which is how NightWatch ties a spike in errors to a deploy.

## Failure injection

The chaos lab changes the SSM parameter `/shoplite/flags` (JSON). The app re-reads it at most every
10 seconds.

| Flag | Effect | Alarm it should trip |
|---|---|---|
| `error_rate` (0–1) | that share of requests fail with `InjectedFaultError` | `shoplite-5xx-rate`, `shoplite-lambda-errors` |
| `extra_latency_ms` | every request waits this long, like a slow dependency | `shoplite-latency-p95` |
| `memory_hog_mb` | each request grabs this much memory (256 MB function) | `shoplite-lambda-errors` |
| `hang` (`true`) | requests sleep past the 5-second timeout | `shoplite-lambda-errors`, `shoplite-latency-p95` |

Other scenarios don't use flags. Throttling lowers the table's write capacity, a broken environment
variable points `TABLE_NAME` at a missing table, and a bad deploy ships a buggy build.

## Deploys

Terraform creates the function once. After that, every merge to main that touches this folder is
deployed by [`deploy.yml`](../.github/workflows/deploy.yml) as a new Lambda version described with
the commit SHA, and the `live` alias moves to it (ADR 0008). A configuration change made by
Terraform, such as memory size, goes live with the next deploy.
