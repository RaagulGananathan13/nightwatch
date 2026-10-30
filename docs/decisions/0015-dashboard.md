# 0015. The web dashboard: CloudFront, private S3, Cognito and a read-only API

- Status: accepted
- Date: 2026-10-30
- Spec sections: F18, 11 (Phase 7)

## Context

On-call engineers need one place to browse incidents, read the evidence behind a diagnosis,
see the postmortems and check the evaluation results. The spec asks for a React app on S3 and
CloudFront with a Cognito login. Everything must stay free, private, and safe to put on the
public internet.

## Decision

1. **The site is served from a private bucket.** S3 holds the built React app. CloudFront
   reads it with origin access control (OAC), and the bucket policy only lets this one
   distribution read. Nothing in the bucket is public.
2. **The API sits behind the same CloudFront.** `/api/*` goes to the `nightwatch-api` Lambda's
   function URL, which uses `AWS_IAM` auth. So only CloudFront, signing with OAC, can call it,
   and there is no public endpoint to attack. Same origin also means no CORS. CloudFront's
   signature takes the Authorization header, so the browser sends its token in
   `X-NightWatch-Token`.
3. **Sign-in is Cognito's hosted page, with PKCE.**
   - The user pool is on the Lite tier (10,000 monthly users free) and has no self sign-up.
     Terraform adds each email, and Cognito sends a temporary password.
   - The app is a public client (no secret) that uses the authorization code flow with PKCE.
     Tokens live in `sessionStorage`, so they last for one browser tab.
   - The API accepts a request only if the token passes four checks:
     - Cognito signed it with RS256, using the pool's published keys;
     - it hasn't expired;
     - it is an access token;
     - it was issued to the dashboard's own app client.
4. **The dashboard reads; it never writes.** Approvals stay in Discord, where every click is
   signature-checked against the service's approvers. A read-only API can't be used to change
   anything, even with a stolen token.
5. **The browser is locked down.**
   - **Strict CSP.** A `<meta>` tag in the built page sets the Content Security Policy. It
     allows scripts and styles only from the site itself, and network calls only to the site
     and Cognito.
   - **Security headers.** CloudFront's managed `SecurityHeadersPolicy` adds HSTS, `nosniff`,
     `X-Frame-Options` (no framing by other sites) and a referrer policy.
   - **Text only.** Postmortems are parsed into React elements, never injected as HTML.
6. **Hash routes** (`#/incidents/<id>`). Every page is the same `index.html`, so S3 needs no
   rewrite rules, and an API error is never mistaken for a missing page.
7. **CI ships it.** A merge that touches `dashboard/` or `eval/results.json` builds the app,
   uploads it, and clears CloudFront's copy of the entry files. Hashed asset files are cached
   for a year. The deploy role gets three permissions for the dashboard:
   - write to its bucket;
   - clear its cache;
   - nothing else.
8. **Evaluation results ship with the build** (`eval-results.json`). They are measured data in
   the repo, so they don't need an API.

## Consequences

- The cost stays at $0:
  - CloudFront's always-free tier is 1 TB and 10 million requests a month;
  - Cognito Lite is free up to 10,000 monthly users;
  - Lambda stays inside its free allowance;
  - S3 holds a few hundred KB.
- No WAF: AWS WAF costs at least $5 a month. The site is static, and the only dynamic path is
  the token-checked, read-only API.
- The default `*.cloudfront.net` certificate means no custom domain, and TLS 1.0 can't be
  switched off. A custom domain with a free ACM certificate would fix both.
- The deployer needed new rights:
  - **CloudFront:** create resources only through us-east-1, CloudFront's API region, and
    manage this account's distributions.
  - **Cognito:** manage user pools in us-east-2.

  The boundary policy's tests guard both.
