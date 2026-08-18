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
//! * **Resumable.** Four hundred megabytes over a connection that drops is the
//!   normal case, not the edge one — the first Windows install died at 57% —
//!   so the partial file is kept and the next attempt asks for the rest with a
//!   Range request. Starting over from zero each time is how a flaky link
//!   becomes an impossible one.

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

/// A file the user can be pointed at, because the interesting part of a failed
/// 400MB download is what happened before the last error — which attempt, how
/// far it got, whether it was the same failure each time.
static LOG: std::sync::OnceLock<std::path::PathBuf> = std::sync::OnceLock::new();

fn log(line: &str) {
    if let Some(path) = LOG.get() {
        if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(path) {
            let _ = std::io::Write::write_all(&mut file, format!("{line}\n").as_bytes());
        }
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
    let _ = LOG.set(app_data.join("runtime-install.log"));
    let base = base_url(version);
    let expected = fetch_checksum(&format!("{base}.sha256"))
        .with_context(|| format!("取不到校验和，这个版本可能还没发布运行时（{version}）"))?;

    // Kept between attempts on purpose: this is where a half-finished download
    // waits to be resumed.
    let scratch = app_data.join("runtime.download");
    fs::create_dir_all(&scratch).context("无法创建下载目录")?;

    let archive = scratch.join("runtime.tar.gz");
    let digest = fetch_with_resume(&format!("{base}.tar.gz"), &archive, &mut on_progress)?;
    if digest != expected {
        // Only now is the partial file worthless: keeping a file whose bytes we
        // know are wrong would make every future attempt resume from garbage.
        let _ = fs::remove_file(&archive);
        bail!("下载的运行时校验失败，已清掉重来；再试一次通常就好了");
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
    let body = http_agent().get(url).call()?.into_string()?;
    body.split_whitespace()
        .next()
        .map(str::to_string)
        .ok_or_else(|| anyhow!("校验和文件是空的"))
}

/// How many times to pick the download back up before giving up on it.
const ATTEMPTS: u32 = 6;

/// A client with timeouts. Without a read timeout a stalled connection hangs
/// the install forever, which from the outside looks exactly like a very slow
/// one — and the user has no way to tell which they are looking at.
fn http_agent() -> ureq::Agent {
    ureq::AgentBuilder::new()
        .timeout_connect(std::time::Duration::from_secs(30))
        .timeout_read(std::time::Duration::from_secs(60))
        .build()
}

/// Download to `to`, resuming whatever is already there, and return the digest
/// of the whole file.
///
/// The hash is computed at the end over the finished file rather than as bytes
/// arrive: a resumed download never sees the earlier bytes, so there is nothing
/// to feed a running hasher.
fn fetch_with_resume(
    url: &str,
    to: &Path,
    on_progress: &mut impl FnMut(u64, u64),
) -> Result<String> {
    let mut last: Option<anyhow::Error> = None;

    for attempt in 0..ATTEMPTS {
        match append_from(url, to, on_progress) {
            Ok(()) => return hash_file(to),
            Err(error) => {
                // A connection that dropped mid-stream leaves bytes worth
                // keeping; back off a little and ask for the rest.
                log(&format!(
                    "第 {} 次尝试中断（{}），{} 秒后从已下载的位置继续",
                    attempt + 1,
                    error,
                    2 * (attempt + 1)
                ));
                last = Some(error);
                std::thread::sleep(std::time::Duration::from_secs(2 * (attempt as u64 + 1)));
            }
        }
    }

    Err(last.unwrap_or_else(|| anyhow!("下载失败")))
        .context(format!("重试 {ATTEMPTS} 次仍未下完"))
}

/// One attempt: ask for everything after what is already on disk.
fn append_from(url: &str, to: &Path, on_progress: &mut impl FnMut(u64, u64)) -> Result<()> {
    let have = fs::metadata(to).map(|m| m.len()).unwrap_or(0);

    let mut request = http_agent().get(url);
    if have > 0 {
        request = request.set("Range", &format!("bytes={have}-"));
    }

    let response = request.call().context("下载失败")?;
    let resuming = response.status() == 206;
    if have > 0 && !resuming {
        // The server ignored the range and is sending the whole file again;
        // start over rather than concatenating two copies.
        let _ = fs::remove_file(to);
        return append_from(url, to, on_progress);
    }

    let remaining: u64 = response
        .header("content-length")
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    let total = if resuming { have + remaining } else { remaining };

    let mut source = response.into_reader();
    let mut file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(to)
        .context("无法写入下载文件")?;

    let mut buffer = vec![0u8; 1 << 20];
    let mut done = have;
    loop {
        let read = source.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        std::io::Write::write_all(&mut file, &buffer[..read])?;
        done += read as u64;
        on_progress(done, total);
    }

    if total > 0 && done < total {
        bail!("连接中断：已下 {done}/{total} 字节");
    }
    Ok(())
}

fn hash_file(path: &Path) -> Result<String> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1 << 20];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
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

#[cfg(test)]
mod resume_tests {
    use super::*;
    use std::io::Write as _;
    use std::net::TcpListener;

    /// A server that serves 8 bytes, honours Range, and hangs up early the
    /// first time — the shape of the failure a real download hits.
    fn flaky_server(body: &'static [u8], cut_first: bool) -> (String, std::thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let handle = std::thread::spawn(move || {
            let mut first = true;
            for stream in listener.incoming().take(2) {
                let mut stream = stream.unwrap();
                let mut head = [0u8; 1024];
                let read = std::io::Read::read(&mut stream, &mut head).unwrap_or(0);
                let request = String::from_utf8_lossy(&head[..read]).to_string();

                let start: usize = request
                    .lines()
                    .find_map(|l| l.strip_prefix("Range: bytes="))
                    .and_then(|v| v.trim_end_matches('-').trim().parse().ok())
                    .unwrap_or(0);
                let slice = &body[start..];

                let status = if start > 0 { "206 Partial Content" } else { "200 OK" };
                let _ = write!(
                    stream,
                    "HTTP/1.1 {status}\r\nContent-Length: {}\r\n\r\n",
                    slice.len()
                );
                // First response stops halfway and closes: a dropped connection.
                let send = if cut_first && first { slice.len() / 2 } else { slice.len() };
                let _ = stream.write_all(&slice[..send]);
                let _ = stream.flush();
                first = false;
            }
        });
        (format!("http://127.0.0.1:{port}/f"), handle)
    }

    #[test]
    fn a_dropped_connection_resumes_instead_of_starting_over() {
        let body: &'static [u8] = b"0123456789abcdef";
        let (url, server) = flaky_server(body, true);
        let dir = std::env::temp_dir().join(format!("d2v-resume-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let file = dir.join("part");

        let mut seen: Vec<u64> = Vec::new();
        let digest = fetch_with_resume(&url, &file, &mut |done, _| seen.push(done)).unwrap();

        assert_eq!(fs::read(&file).unwrap(), body, "文件应该是完整的");
        assert_eq!(digest, hash_file(&file).unwrap());
        // Progress must never jump backwards: the second attempt starts from
        // what was already on disk, which is the whole point.
        assert!(seen.windows(2).all(|w| w[1] >= w[0]), "进度回退了：{seen:?}");
        assert!(seen.iter().any(|&d| d > 0 && d < body.len() as u64));

        drop(server);
        let _ = fs::remove_dir_all(&dir);
    }
}
