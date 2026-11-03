# Contributing to NightWatch

Thanks for helping. Bug reports, docs fixes, new runbooks and new tools are all welcome.

## Before you start

For anything bigger than a small fix, open an issue first, so we can agree on the approach
before you spend time on it. For a security problem, follow the
[security policy](SECURITY.md) instead of opening an issue.

## Set up

You need Python 3.13, Git and GNU make. On Windows, run `make` from Git Bash.

```bash
make install    # a virtual environment, the dev tools, and the git hooks
make check      # lint, types and tests: the same checks as CI
```

You don't need an AWS account. The tests use fake AWS and LLM clients.

## Rules every change keeps

- **No secrets, ever.** Tokens, webhooks, keys and account ids live in SSM Parameter Store in
  your own account, never in code, tests or docs. The git hooks look for private keys and AWS
  keys, and CI runs Gitleaks over the whole history. Tests build credential-shaped strings at
  runtime, so scanners don't flag the source.
- **It must stay free.** Use only AWS always-free services. Any new alarm or table capacity
  must pass the free-tier guard in `infra/envs/dev`.
- **The investigator only reads.** Tools the agent can call must not change anything. Fixes
  go only through reviewed runbooks, behind a person's **Approve**.
- **Least privilege.** A new permission names exact actions and resources, inside the
  NightWatch permissions boundary.

## Pull requests

- Keep each one small and focused, with tests for new behaviour.
- `make check` must pass. On a pull request from a fork, CI runs lint, types, tests and
  security scans, with no secrets and no AWS access. The maintainer approves the run first.
- Changes to `.github/`, `infra/`, `runbooks/` or `nightwatch/remediation/` get an extra careful
  review, because they decide what NightWatch may do in an AWS account.
- Write short commit messages in plain English that say what changed.

## Be kind

Assume good intent, keep feedback about the code, and help newcomers find their feet.

By contributing, you agree that your work is licensed under the
[Apache License 2.0](LICENSE).
