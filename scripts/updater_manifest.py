"""Build the manifest the desktop app polls to learn a newer version exists.

Tauri's updater fetches one JSON document and looks up its own platform key in
it. Everything else in the release — the dmg someone downloads by hand, the
runtime tarball — is invisible to it; only what is listed here can ever be
offered as an update.

So the rule this enforces is: a platform appears only when both halves are
present, the package and its signature. A platform listed without a valid
signature is worse than one left out, because the app will find the entry,
download the package, fail the signature check, and report a broken update
rather than "already up to date".

Usage: updater_manifest.py <artifacts-dir> <version> <output> [built-targets]

``built-targets`` is the comma-separated list of targets the run set out to
build. It has to come from the workflow because nothing in the artifacts can
say it: a build that failed uploads no directory, and a directory that is
absent because the job died looks exactly like one absent because the platform
was never in the matrix. Only the first of those deserves a warning.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Keyed by the build job's artifact directory, not by guessing at file names.
# The build already knows which platform it ran on; a filename does not
# reliably say so — macOS ships its updater bundle as a bare
# `Doc2Video.app.tar.gz`, with neither version nor architecture in it, and
# matching on "aarch64" silently left macOS out of the manifest entirely.
#
# Within a platform the suffix picks which of its bundles can install over a
# running app. Windows is NSIS rather than MSI on purpose: the updater can run
# an NSIS installer silently, and cannot do the same with an MSI.
PLATFORMS: dict[str, tuple[str, str]] = {
    "macos-arm64": ("darwin-aarch64", ".app.tar.gz"),
    "macos-x64": ("darwin-x86_64", ".app.tar.gz"),
    "linux-x64": ("linux-x86_64", ".AppImage"),
    "windows-x64": ("windows-x86_64", "-setup.exe"),
}

RELEASE = "https://github.com/VitaTsui/doc2video-agent/releases/download"


def warn(message: str) -> None:
    """A line on the run's summary, not just in a log nobody opens."""
    print(f"::warning::{message}")


def find(root: Path, target: str, suffix: str) -> Path | None:
    """That platform's updatable bundle, from that platform's own artifacts."""
    for directory in root.glob(f"*{target}*"):
        for file in sorted(directory.rglob("*")):
            if file.is_file() and file.name.endswith(suffix):
                return file
    return None


def main() -> int:
    artifacts, version, output = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    declared = sys.argv[4] if len(sys.argv) > 4 else ""
    expected = {t.strip() for t in declared.split(",") if t.strip()}

    platforms: dict[str, dict[str, str]] = {}
    for target, (key, suffix) in PLATFORMS.items():
        package = find(artifacts, target, suffix)
        if package is None:
            if target in expected:
                # Loud, because the manifest job runs even when a build failed
                # or was cancelled — publishing what exists is right, doing it
                # quietly is not. A platform missing from the manifest is a
                # platform whose users never hear about the update.
                warn(f"{key}（{target}）本次要构建却没有产物，这个平台收不到更新")
            else:
                print(f"{key}（{target}）不在本次构建范围内，跳过")
            continue
        signature = package.with_name(package.name + ".sig")
        if not signature.is_file():
            # Built without the signing key. Listing it would hand the app a
            # download it is bound to reject.
            warn(f"{key}：{package.name} 没有签名，这个平台收不到本次更新")
            continue
        platforms[key] = {
            "signature": signature.read_text(encoding="utf-8").strip(),
            "url": f"{RELEASE}/v{version}/{package.name}",
        }
        print(f"{key}：{package.name}")

    if not platforms:
        print("没有任何平台可以更新——不写清单，免得覆盖掉上一个能用的。")
        return 1

    output.write_text(
        json.dumps(
            {
                "version": version,
                "notes": f"详见 https://github.com/VitaTsui/doc2video-agent/releases/tag/v{version}",
                "platforms": platforms,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
