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

Usage: updater_manifest.py <artifacts-dir> <version> <output>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Which artifact serves which platform. The app asks by target triple; these
# are the names Tauri gives the bundles it can install over itself.
#
# Windows is NSIS rather than MSI on purpose: the updater can run an NSIS
# installer silently over a running app, and cannot do the same with an MSI.
PLATFORMS: list[tuple[str, tuple[str, ...]]] = [
    ("darwin-aarch64", ("aarch64.app.tar.gz", "_aarch64.app.tar.gz")),
    ("darwin-x86_64", ("x64.app.tar.gz", "x86_64.app.tar.gz")),
    ("linux-x86_64", ("amd64.AppImage", "x86_64.AppImage")),
    ("windows-x86_64", ("x64-setup.exe", "x64_en-US.msi")),
]

RELEASE = "https://github.com/VitaTsui/doc2video-agent/releases/download"


def find(files: list[Path], suffixes: tuple[str, ...]) -> Path | None:
    for suffix in suffixes:
        for file in files:
            if file.name.endswith(suffix):
                return file
    return None


def main() -> int:
    artifacts, version, output = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    files = [p for p in Path(artifacts).rglob("*") if p.is_file()]

    platforms: dict[str, dict[str, str]] = {}
    for key, suffixes in PLATFORMS:
        package = find(files, suffixes)
        if package is None:
            print(f"{key}：没有可更新的包，跳过")
            continue
        signature = package.with_name(package.name + ".sig")
        if not signature.is_file():
            # Built without the signing key. Listing it would hand the app a
            # download it is bound to reject.
            print(f"{key}：{package.name} 没有签名，跳过")
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
