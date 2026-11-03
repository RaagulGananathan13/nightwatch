# Quickstart

## Try it in five minutes (no AWS needed)

You need Python 3.13, Git and `make` (on Windows, use Git Bash).

```bash
git clone https://github.com/RaagulGananathan13/nightwatch.git
cd nightwatch
make install        # a virtual environment with every dependency
make check          # lint, types and over 300 tests, in about a minute
```

The tests replay a recorded bad-deploy incident with fake AWS and LLM clients. To watch the
agent work through it, and then the guard catching a made-up number, run:

```bash
.venv/Scripts/python -m pytest tests/unit/nightwatch/test_agent.py -v   # .venv/bin/python on macOS and Linux
```

Then read what happened on real AWS:
- the [evaluation](https://github.com/RaagulGananathan13/nightwatch/blob/main/eval/results.md):
  7 injected faults, 7 root causes found;
- the live runs in the
  [README](https://github.com/RaagulGananathan13/nightwatch#measured-so-far).

## Deploy it (about 30 minutes, mostly waiting for AWS)

Everything stays in AWS's always-free tier, and a $1 budget alert watches it. The
[infrastructure guide](https://github.com/RaagulGananathan13/nightwatch/blob/main/infra/README.md)
has every step. In short:

1. **Make the deployer.** As the account owner, in AWS CloudShell, run
   `bash nightwatch/infra/bootstrap/create-deployer.sh`. It makes a restricted user whose
   permissions boundary caps everything NightWatch can ever do.
2. **Make the state bucket:** `make bootstrap-plan`, then `make bootstrap-apply`.
3. **Store your secrets in SSM yourself.** You need a Discord webhook and a free Groq API key.
   They never pass through Terraform or Git.
4. **Build it:** `make init`, then `make plan` (read it), then `make apply`.
5. **Break something on purpose:** `make chaos NAME=bad-deploy`. Within about a minute,
   Discord shows the incident and its diagnosis.

Optional extras, each in the guide:
- approval buttons, by adding a Discord bot;
- the `/nw` commands;
- the web dashboard;
- Slack;
- the MCP server.

Then [connect another account](connect.md).
