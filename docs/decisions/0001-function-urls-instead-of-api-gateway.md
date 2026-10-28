# 0001. Lambda Function URLs instead of API Gateway

- Status: accepted
- Date: 2026-10-28
- Spec sections: 5, 6, 8, 12

## Context

The spec puts API Gateway in front of ShopLite, the chat interactions endpoint and the web API.
API Gateway's HTTP API free tier only lasts 12 months for new customers (1 million calls per
month), then costs about $1.00 per million requests. Accounts on AWS's 2025 Free plan get
credits instead of a 12-month tier. NightWatch has a hard $0 constraint.

## Decision

Expose HTTPS endpoints with **Lambda Function URLs**. They cost nothing beyond Lambda itself,
which is always free up to 1 million requests and 400,000 GB-seconds a month.

- **ShopLite** uses `AWS_IAM` auth. The load, chaos and eval scripts sign requests with SigV4.
  Unsigned traffic is rejected before the function runs, so nobody can spam the endpoint into
  the paid tier.
- **Discord interactions** and the **`/alerts` webhook** use `NONE` auth, and every request is
  verified in code: an Ed25519 signature for Discord, HMAC-SHA256 for webhooks.
- The **dashboard API** uses `NONE` auth plus a Cognito JWT that is verified in code. CORS is
  configured on the URL.

Alarms use Lambda's function URL metrics (`UrlRequestCount`, `Url5xxCount`,
`UrlRequestLatency`) instead of API Gateway's.

## Consequences

- Guaranteed $0, and fewer moving parts.
- No API Gateway throttling, request validation or usage plans, so input validation happens in
  code.
- Each function has its own URL. API Gateway can return later as an opt-in module for teams that
  want it.
