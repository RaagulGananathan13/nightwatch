# Quickstart

Try NightWatch on your own computer in about five minutes. You need no AWS account and no API
keys: the tests use fake AWS and language-model clients.

## Run the tests

You need Python 3.13, Git and `make`. On Windows, run these from Git Bash.

```bash
git clone https://github.com/RaagulGananathan13/nightwatch.git
cd nightwatch
make install        # a virtual environment with every dependency
make check          # lint, strict type checks and over 300 tests, in about a minute
```

## Watch an investigation

The tests replay a recorded bad-deploy incident. To watch the agent work through it, and the
guard catch a made-up number in a diagnosis, run:

=== "Windows"

    ```bash
    .venv/Scripts/python -m pytest tests/unit/nightwatch/test_agent.py -v
    ```

=== "macOS and Linux"

    ```bash
    .venv/bin/python -m pytest tests/unit/nightwatch/test_agent.py -v
    ```

## See it on real AWS

- The [evaluation](https://github.com/RaagulGananathan13/nightwatch/blob/main/eval/results.md):
  seven injected faults, seven root causes found.
- The live runs in the
  [README](https://github.com/RaagulGananathan13/nightwatch#measured-so-far).

## Next steps

To run NightWatch in your own AWS account:

1. Check the [prerequisites](prerequisites.md).
2. Follow [Deploy to AWS](deploy.md). It takes about 30 minutes, mostly waiting for AWS.
3. Add [approval buttons and Slack](chat.md).
