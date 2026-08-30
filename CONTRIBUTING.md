# Contributing

## Reporting something

Bug reports are welcome as [issues](https://github.com/mangokingTW/wintegrate/issues).
The useful ones say which Windows edition and architecture, which Python version,
and what the library did versus what you expected — this project exists because
Windows automation fails in environment-specific ways, so the environment is
usually the load-bearing detail.

Suspected vulnerabilities go through [SECURITY.md](SECURITY.md) instead, not a
public issue.

## Working on the code

```bash
git clone https://github.com/mangokingTW/wintegrate.git
cd wintegrate
pip install -e .[dev]
```

The package imports on macOS and Linux — every Win32 and UIA call raises a clear
unsupported-platform error instead — so linting, the parser tests, and the
encoder tests all run off-Windows. Anything that drives a real window does not:

```bash
pytest tests/ -v          # live GUI tests need Windows
ruff check src/ tests/
ruff format --check src/ tests/
```

Run **both** ruff commands. CI runs both, and `check` passing does not imply
`format --check` passes.

## What CI verifies

Every pull request runs the full suite against **real GUIs** on Windows x64 and
Windows ARM64, across Python 3.11–3.14, plus CodeQL. The tests launch Notepad,
Calculator, and a Win32 dialog fixture and drive them for real; there are no
mocks standing in for the Windows APIs.

That is slow, and deliberately so. The failures this library exists to prevent —
dropped keystrokes, stale COM wrappers, windows that take longer to appear than
anyone budgeted for — only reproduce against a live desktop.

## Writing tests

Prefer a test that would have failed before your change. Several tests here exist
because a plausible-looking assertion turned out to prove nothing:
`Ctrl+A` does not select all in a classic Win32 `EDIT`, so an assertion built on
it passed for the wrong reason; a line-count delta stays correct while the typed
text is mangled. If a test cannot fail, it is documentation with a runtime cost.

Where the logic is pure — the key-spec parser, line counting — property-based
tests (Hypothesis) tend to find more than examples do.

## Pull requests

- Keep the branch focused; unrelated cleanup belongs in its own PR.
- Explain *why* in the commit message. What changed is visible in the diff.
- Open as a draft and mark it ready once CI is green.
