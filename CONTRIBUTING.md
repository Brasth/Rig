# Contributing

Branch from `main`, keep docs and protocol text accurate, and open a focused pull request.

- Do not invent APIs or change documented parent/worker behavior without a matching implementation.
- README above-the-fold is for first-time visitors. Keep Install, Smart routing, Adaptive workflows, and Verification details intact below the fold.
- Deeper contracts live in `docs/usage.md`, `docs/smart-routing.md`, and `.agents/skills/delegate-harness/SKILL.md`.

## Managed agent guidance

Edit `docs/agent-protocol.md`, then run `python3 scripts/generate_protocol.py`.
Do not separately edit the generated AGENTS block, delegation bootstrap or topic
references. `python3 scripts/generate_protocol.py --check` detects drift without
writing; the offline test suite also checks generation, safety-invariant coverage
and isolated installation. See [the read contract and migration map](docs/agent-guidance.md).

## Test baseline

Use Python 3.11 or newer, Git, Bash, and Node.js. CI explicitly tests Python
3.12 and Node.js 22 on Ubuntu 24.04 and macOS 14. The Python tests use the
standard library; the Mermaid renderer is bundled. No `pip install`, `npm
install`, Rig initialization, provider login, or API key is needed. Optional
real-tmux checks run when tmux is available; their absence is reported as a
unittest skip. Fake-provider integration tests run on both platforms.

Run the same checks as CI from the repository root:

```sh
python3 tests/run_tests.py
```

The runner checks every tracked/non-ignored Python source with `compile`, checks
shell scripts (including `bin/rig`) with `bash -n`, then runs full standard
unittest discovery with the `test_*.py` pattern. Allow several minutes; the
runner has a 15-minute overall timeout. Logs are streamed and saved in the
printed temporary directory, including on failure. Useful focused commands:

```sh
python3 tests/run_tests.py --syntax-only
python3 tests/run_tests.py --pattern 'test_token_usage.py'
python3 tests/run_tests.py --timeout 900 --log-dir /tmp/rig-test-logs
```

The test process starts with a fresh environment, temporary `HOME`/`RIG_HOME`
and temporary config/cache directories. It does not inherit provider keys,
agent-session variables, Git credentials, Python/Node hooks, or user shell
configuration. Its PATH contains the chosen Python, Node, Git, optional tmux,
and system tools, without user/agent binary directories. It refuses a system
PATH containing known provider/browser-agent commands. Model catalog refresh,
update checks, and optional installer probes are disabled by default; fixtures
may explicitly exercise those paths with fake executables.

Remote Git transports and ordinary download/package-manager commands are
blocked; the supported `curl -fsSL file:///... -o PATH` fixture transport copies
local bytes only. Python and Node guards reject outbound connections while
preserving loopback TCP and Unix sockets needed by the integration tests. These safeguards
prevent accidental external calls; they are not an OS security sandbox for
untrusted test code. Tests that deliberately replace their environment or use
absolute tools must still supply fakes and must never contact a live provider.
Use a disposable machine/container for unfamiliar code.

Some existing installer tests deliberately set `RIG_HOME` to the checkout and
can leave generated `install-manifest.json`, lock files, UI state, or ignored
integration files there. Do not run the suite in your installed Rig directory
or run multiple suites against the same checkout. For a pristine committed
baseline, use a disposable worktree (uncommitted edits are not included):

```sh
scratch=$(mktemp -d)
git worktree add --detach "$scratch/checkout" HEAD
(cd "$scratch/checkout" && python3 tests/run_tests.py)
# After inspecting the result, remove only this disposable checkout:
git worktree remove --force "$scratch/checkout"
rmdir "$scratch"
```

Never run a blanket `git clean -fdx` in your development checkout to clear test
state. Tests should create explicit enabled-project fixtures when required;
lifecycle tests must retain their uninitialized/disabled-project assertions.
Keep real-provider experiments separate from this baseline.

`.github/workflows/tests.yml` runs on pull requests and pushes to `main`, with
SHA-pinned official GitHub actions, read-only repository permissions, no
persisted checkout credentials, no vendor
secrets, and no publish step. It uploads each platform's logs on success or
failure. Workflow checkout/runtime setup and artifact upload use GitHub's
network; the test phase does not need external services.
