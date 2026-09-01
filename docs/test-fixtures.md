# The application mirror

`wintegrate`'s tests drive four real applications, and its upstream-bug demos
drive eight more builds of them. Every one of those packages is fetched from
[`mangokingTW/wintegrate-test-fixtures`][mirror] rather than from its publisher.

[mirror]: https://github.com/mangokingTW/wintegrate-test-fixtures/releases

## Why mirror at all

Files forced the question. Every GitHub release of `files-community/Files` carries
**zero assets** — the packages exist only on the CDN named in the project's own
`cd-sideload-stable.yml`, and that CDN sits behind Cloudflare bot protection. A
runner's datacentre IP gets a `Just a moment...` challenge page instead of the
100 MB bundle. (The small `.appinstaller` manifest comes through fine; the
bundle does not.) Solving a bot challenge is not a test workflow's job.

The other three followed for a duller reason. A release gate that reaches out to
four separate third-party hosts on every push fails whenever any one of them is
having a bad day — and **none of those failures says anything about this
library**. The same argument is sharper for the bug demos: those pin *old*
releases, and an old release is exactly the kind of asset that quietly stops
being served.

## Verified, not trusted

Mirroring moves the bytes into an account you control, which is a supply-chain
problem, not a solution — unless the bytes are checked.

| check | what it pins |
|---|---|
| **SHA-256** | the exact bytes. Every hash here was produced **twice, from two independent downloads on different machines**, and they agreed. |
| **Authenticode subject** | the publisher, so the bytes stay attributable even though they now come from somebody else's release page. |

Two decisions worth stating:

**The subject is compared whole, not by prefix.** A prefix match on
`CN=Some Publisher` also accepts `CN=Some Publisher Ltd`.

**Assets that upstream does not sign have no signer pinned.** The DB Browser
arm64 msi is unsigned, and no zip can carry an Authenticode signature. There the
hash is the only check available, and the workflow prints exactly that. Pinning
a signature that does not exist would fail every run; quietly treating those
assets as equally verified would be worse.

!!! warning "Copy the subject from a runner's log"

    Authenticode subjects are not ASCII. Notepad++'s carries `S=Île-de-France`,
    and the first version of this pinned `Ile-de-France` — the console that
    captured the subject had stripped the accent. Both Notepad++ jobs then failed
    with `unexpected signer` against a subject that was otherwise identical.

## What is mirrored

### The release gate

These four run on every push, on both `windows-latest` and `windows-11-arm`.

| app | release tag | signed |
|---|---|---|
| Notepad++ 8.9.8 | `notepadpp-8.9.8` | ✅ `CN="NOTEPAD++"` |
| WinMerge 2.16.58.2 | `winmerge-2.16.58.2` | ✅ `CN=Takashi Sawanaka` |
| DB Browser for SQLite 3.13.1 | `sqlitebrowser-3.13.1` | ✅ win64 only — **the arm64 msi is unsigned upstream** |
| Files 4.2.9.0 | `files-4.2.9.0` | ✅ `CN=Yair Aichenbaum` |

DB Browser ships a different Qt per architecture at the *same* application
version — win64 carries `Qt5Core.dll`, arm64 carries `Qt6Core.dll` — so both are
kept. Qt 6 exposes `SelectionItem` on tab items and Qt 5 does not; running only
one would leave half the users untested.

### The bug demos

Each pair is one fixed upstream issue: the build that had it, and the build that
fixed it. These run on request, not on every push.

| issue | had the bug | fixed | what changes |
|---|---|---|---|
| Notepad++ [#16326][npp] | `notepadpp-8.7.9` | `notepadpp-8.8` | Ctrl+Shift+D inserts an invisible `0x04` |
| DB Browser [#3735][db4s] | `sqlitebrowser-3.13.0` | `sqlitebrowser-3.13.1-portable` | copying one cell appends a trailing newline |
| WinMerge [#3015][wm] | `winmerge-2.16.52` | `winmerge-2.16.52.2` | "Insert tabs" resets on leaving Options |
| Files [#18820][files] | `files-4.2.7.0` | `files-4.2.9.0` | rename-flyout accelerators do not fire |

[npp]: https://github.com/notepad-plus-plus/notepad-plus-plus/issues/16326
[db4s]: https://github.com/sqlitebrowser/sqlitebrowser/issues/3735
[wm]: https://github.com/WinMerge/winmerge/issues/3015
[files]: https://github.com/files-community/Files/issues/18820

Portable packages are used for the demos wherever they exist, for two reasons:
two versions of an installed application cannot be present at once, and for
Notepad++ **the fix itself is in the portable package's `config.xml`**.

!!! note "Pick the pair from the fix, not from the report"

    WinMerge's was mirrored as 2.16.52.2/2.16.53 first, because #3055 was filed
    two days after 2.16.52.2 shipped. Both builds then behaved correctly — the
    maintainer had closed #3055 as a duplicate of **#3015**, already *fixed in*
    2.16.52.2. The real pair is 2.16.52/2.16.52.2. A report's date tells you when
    somebody noticed; only the fix tells you where the boundary is.

Where the two versions must be the same build flavour for the comparison to mean
anything, they are. DB Browser 3.13.0 has no arm64 package at all, so both sides
of that pair are the win64 (Qt 5) zip — comparing a Qt 5 build against a Qt 6
build would be measuring the Qt version, not the fix.

## Adding to the mirror

1. Download the asset from upstream and record its SHA-256.
2. Download it **again, on a different machine**, and confirm the hash matches.
   One download tells you what you got; two tell you what upstream is serving.
3. Get the Authenticode subject with `Get-AuthenticodeSignature` on Windows, from
   a console running a UTF-8 code page — or read it out of a CI log.
4. Publish a release whose notes record the upstream URL, the hash, and the
   signer, so the provenance travels with the bytes.
5. Add the tag, asset name, hash and signer to the workflow matrix. Leave
   `fixtureSigner` empty if upstream does not sign it.
