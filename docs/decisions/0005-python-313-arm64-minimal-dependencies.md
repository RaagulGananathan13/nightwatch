# 0005. Python 3.13 on arm64 with minimal dependencies

- Status: accepted
- Date: 2026-10-28
- Spec sections: 6, 19

## Context

The spec says Python 3.12. The dev machine and CI run 3.13, and AWS Lambda's `python3.13`
runtime (Amazon Linux 2023) stays supported longer than 3.12. Small Lambda packages start
faster, and the spec asks for minimal dependencies.

## Decision

- Use the `python3.13` runtime on **arm64**. Graviton gets the same free tier and costs about
  20% less per GB-second if usage ever goes past it.
- Put shared code in a Lambda layer.
- Call LLM HTTP APIs with the standard library (`urllib.request`), not vendor SDKs. `boto3`
  comes from the Lambda runtime.
- Keep runtime dependencies to small, well-known libraries such as pydantic, PyYAML and PyNaCl.
  Install them as manylinux aarch64 wheels with `pip install --platform`, so builds from
  Windows and from Linux CI are identical.
- Force LF line endings in `.gitattributes`. Otherwise zips built on Windows and Linux hash
  differently and Terraform redeploys every time.

## Consequences

- Small packages, fast cold starts and reproducible builds.
- Each LLM provider needs its own small HTTP client. These are covered by tests that replay
  recorded responses.
