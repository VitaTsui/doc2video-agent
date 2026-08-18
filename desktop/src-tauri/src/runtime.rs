//! Fetching the part of the app that does the work.
//!
//! The installer carries a window and little else. The interpreter, the
//! pipeline, ffmpeg, a voice and a font — four hundred megabytes of it — arrive
//! on first launch and stay until the app's version changes. Keeping them out
//! of the installer is what lets a fix to a button be a few megabytes instead
//! of a re-download of everything.
//!
//! The runtime's version is the app's version, so there is no manifest to keep
//! in sync and no way for the two to disagree: the URL is derived, and a build
//! that ships without its runtime published fails loudly at first launch rather
//! than quietly running last release's pipeline.
//!
//! Three properties this has to hold, none of them optional:
//!
//! * **Verified before it is trusted.** The checksum is fetched separately and
//!   checked before anything is unpacked. A truncated download that unpacks
//!   anyway is a half-installed interpreter and a support case nobody can read.
//! * **Atomic.** It unpacks beside the live directory and swaps at the end, so
//!   a download killed halfway leaves the previous runtime intact.
//! * **Resumable by restarting.** No partial state is kept; a failed attempt
//!   deletes its scratch and the next launch starts over.

use std::fs;
use std::io::Read;
use std::path::Path;

use anyhow::{anyhow, bail, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const REPO: &str = "https://github.com/VitaTsui/doc2video-agent";

/// What the app knows about its runtime.
#[derive(Serialize, Clone)]
pub struct Status {
    /// Present and matching this build.
    pub ready: bool,
    /// The version installed, if any — shown when it is the wrong one.
    pub installed: Option<String>,
    pub required: String,
    pub target: String,
    /// Roughly how much a first install downloads, for the screen that asks.
    pub approx_mb: u32,
}

#[derive(Serialize, Deserialize)]
struct Manifest {
    version: String,
    #[serde(default)]
    target: String,
}

/// The triple the build script publishes under. Kept in one place because a
/// mismatch here is a 404 at first launch on exactly one platform.
pub fn target() -> String {
    let os = if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(target_os = "windows") {
        "windows"
    } else {
        "linux"
    };
    let arch = if cfg!(target_arch = "aarch64") {
        "arm64"
    } else {
        "x64"
    };
    format!("{os}-{arch}")
}

pub fn status(app_data: &Path, required: &str) -> Status {
    let installed = read_manifest(&app_data.join("runtime")).map(|m| m.version);
    Status {
        ready: installed.as_deref() == Some(required),
        installed,
        required: required.to_string(),
        target: target(),
        approx_mb: 400,
    }
}

fn read_manifest(runtime: &Path) -> Option<Manifest> {
    let text = fs::read_to_string(runtime.join("runtime.json")).ok()?;
    serde_json::from_str(&text).ok()
}

fn base_url(version: &str) -> String {
    format!("{REPO}/releases/download/v{version}/d2v-runtime-{version}-{}", target())
}

/// Download, verify, unpack, swap. `on_progress` is called with (done, total)
/// bytes; total is 0 while the server has not said how big it is.
pub fn install(
    app_data: &Path,
    version: &str,
    mut on_progress: impl FnMut(u64, u64),
) -> Result<()> {
    let base = base_url(version);
    let expected = fetch_checksum(&format!("{base}.sha256"))
        .with_context(|| format!("取不到校验和，这个版本可能还没发布运行时（{version}）"))?;

    let scratch = app_data.join("runtime.download");
    let _ = fs::remove_dir_all(&scratch);
    fs::create_dir_all(&scratch).context("无法创建下载目录")?;

    let archive = scratch.join("runtime.tar.gz");
    let digest = download(&format!("{base}.tar.gz"), &archive, &mut on_progress)?;
    if digest != expected {
        let _ = fs::remove_dir_all(&scratch);
        bail!("下载的运行时校验失败（可能没下完），请重试");
    }

    unpack(&archive, &scratch)?;
    let unpacked = scratch.join("runtime");
    if !unpacked.join("runtime.json").exists() {
        let _ = fs::remove_dir_all(&scratch);
        bail!("运行时包结构不对：缺少 runtime.json");
    }

    // Swap last: until this line the previous runtime is still the live one.
    let live = app_data.join("runtime");
    let retired = app_data.join("runtime.old");
    let _ = fs::remove_dir_all(&retired);
    if live.exists() {
        fs::rename(&live, &retired).context("无法移开旧的运行时")?;
    }
    fs::rename(&unpacked, &live).context("无法启用新的运行时")?;
    let _ = fs::remove_dir_all(&retired);
    let _ = fs::remove_dir_all(&scratch);
    Ok(())
}

fn fetch_checksum(url: &str) -> Result<String> {
    let body = ureq::get(url).call()?.into_string()?;
    body.split_whitespace()
        .next()
        .map(str::to_string)
        .ok_or_else(|| anyhow!("校验和文件是空的"))
}

/// Stream to disk while hashing, so the bytes are never held in memory and
/// never read twice.
fn download(url: &str, to: &Path, on_progress: &mut impl FnMut(u64, u64)) -> Result<String> {
    let response = ureq::get(url).call().context("下载失败")?;
    let total: u64 = response
        .header("content-length")
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);

    let mut source = response.into_reader();
    let mut file = fs::File::create(to).context("无法写入下载文件")?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1 << 20];
    let mut done: u64 = 0;

    loop {
        let read = source.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
        std::io::Write::write_all(&mut file, &buffer[..read])?;
        done += read as u64;
        on_progress(done, total);
    }

    Ok(format!("{:x}", hasher.finalize()))
}

fn unpack(archive: &Path, into: &Path) -> Result<()> {
    let file = fs::File::open(archive)?;
    let decoder = flate2::read::GzDecoder::new(file);
    let mut tar = tar::Archive::new(decoder);
    // Preserve the executable bits: without them the interpreter unpacks as a
    // file the system will not run.
    tar.set_preserve_permissions(true);
    tar.unpack(into).context("解压失败")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_target_is_one_of_the_names_the_build_script_publishes() {
        let name = target();
        assert!(
            ["macos-arm64", "macos-x64", "windows-x64", "linux-x64", "linux-arm64"]
                .contains(&name.as_str()),
            "未知的 target：{name}"
        );
    }

    #[test]
    fn a_missing_runtime_is_reported_rather_than_assumed() {
        let status = status(Path::new("/nonexistent"), "9.9.9");
        assert!(!status.ready);
        assert_eq!(status.installed, None);
        assert_eq!(status.required, "9.9.9");
    }

    #[test]
    fn the_url_names_the_version_and_the_platform() {
        let url = base_url("1.2.3");
        assert!(url.contains("/v1.2.3/"), "{url}");
        assert!(url.ends_with(&format!("d2v-runtime-1.2.3-{}", target())), "{url}");
    }
}
