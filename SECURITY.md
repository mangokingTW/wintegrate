# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately, not as a public issue.

Use GitHub's private reporting on this repository — the **Security** tab →
**Report a vulnerability**
(<https://github.com/mangokingTW/wintegrate/security/advisories/new>).
That keeps the report visible only to the maintainer until a fix is out.

Expect an acknowledgement within a few days. This is a personal open-source
project maintained in spare time, so there is no paid support or formal SLA, but
security reports are taken seriously and prioritised over feature work.

There is no bounty programme.

## Supported versions

Only the [latest release](https://github.com/mangokingTW/wintegrate/releases/latest)
is supported. Fixes ship in a new release rather than as patches to older ones.

## Verifying a download

Every release is built by a GitHub Actions workflow and published to PyPI through
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) — no API token is
stored anywhere, so there is no long-lived credential to steal.

The published files carry two independent attestations:

**PyPI attestations (PEP 740).** Each file on
<https://pypi.org/project/wintegrate/> shows the repository, workflow, and commit
it was built from. PyPI verifies the signature itself; nothing to run locally.

**GitHub build provenance.** Verify a downloaded file against the repository:

```bash
pip download --no-deps --no-binary :all: wintegrate   # or fetch the wheel
gh attestation verify wintegrate-<version>.tar.gz --repo mangokingTW/wintegrate
```

A pass means that exact file was produced by this repository's release workflow
from a specific commit. It does not vouch for the code being free of defects — it
proves the artifact you have is the artifact that was built here, which is the
part reading the source cannot tell you.

## What this library does by design

`wintegrate` automates the Windows desktop: it synthesizes keyboard and mouse
input, reads window and control contents through UI Automation, captures the
screen, and can terminate processes. Those are the capabilities it exists to
provide, and they are indistinguishable from what unwanted software does.

Two behaviours are worth knowing before you run it unattended:

- **Runner sanitization** (`SessionConfig.sanitize_runner`) force-terminates
  background processes — Windows Terminal, Edge, WSL, and leftover Notepad or
  Calculator instances. It defaults to CI environments only (`CI` or
  `GITHUB_ACTIONS` set truthy) and never targets the current process or its
  ancestors. Setting it to `True` on a workstation will close those applications.
- **Screen recording** captures the entire primary display, including anything
  else visible on it. Artifacts are written to the configured `artifact_dir`;
  treat them as potentially sensitive before uploading them anywhere.
