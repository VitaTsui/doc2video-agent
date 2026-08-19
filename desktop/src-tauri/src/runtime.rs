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

/// Which heavy tree this build needs, as a digest of what decides its contents.
///
/// Written by `scripts/build_runtime.py --print-base-version` and checked
/// against it by a test, because the two must never drift: a shell asking for
/// a base that was never published cannot install at all, and one asking for
/// an older base than its app needs fails later and less clearly.
const BASE_VERSION: &str = include_str!("../base_version.txt");

/// Where the heavy half lives. A release of its own, named by the digest, so
/// two app releases that did not move a dependency share one 400MB file and
/// the second costs nobody anything.
fn base_url(base: &str) -> String {
    format!("{REPO}/releases/download/runtime-base-{base}/d2v-base-{base}-{}", target())
}

fn app_url(version: &str) -> String {
    format!("{REPO}/releases/download/v{version}/d2v-app-{version}-{}", target())
}

/// Which half of the install is running.
///
/// They are reported apart because they feel nothing alike: the download is
/// bytes over a network and has a real total, the unpack is twenty thousand
/// files against a disk and an antivirus. Folding them into one bar means the
/// bar stops for minutes and the person watching concludes it crashed.
#[derive(Serialize, Clone, Copy)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Download,
    Unpack,
}

/// What the app knows about its runtime.
#[derive(Serialize, Clone)]
pub struct Status {
    /// Present and matching this build.
    pub ready: bool,
    /// The version installed, if any — shown when it is the wrong one.
    pub installed: Option<String>,
    pub required: String,
    pub target: String,
    /// Roughly how much this particular install will download. The number the
    /// screen shows has to be the real one: promising 400MB and taking two
    /// seconds is merely odd, promising 2MB and taking an hour is a betrayal.
    pub approx_mb: u32,
    /// Whether the heavy half has to come down too. False for the ordinary
    /// case — a new release against a base that has not moved.
    pub needs_base: bool,
}

#[derive(Serialize, Deserialize)]
struct Manifest {
    version: String,
    #[serde(default)]
    target: String,
    /// Absent in runtimes installed before the split; such a tree cannot be
    /// told apart from one built against a different base, so it is replaced.
    #[serde(default)]
    base: String,
    #[serde(default)]
    app: String,
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
    let manifest = read_manifest(&app_data.join("runtime"));
    let base = manifest.as_ref().map(|m| m.base.as_str()).unwrap_or("");
    let app = manifest.as_ref().map(|m| m.app.as_str()).unwrap_or("");

    let needs_base = base != BASE_VERSION.trim();
    let ready = !needs_base && app == required;
    Status {
        ready,
        installed: manifest.as_ref().map(|m| m.version.clone()),
        required: required.to_string(),
        target: target(),
        approx_mb: if needs_base { 400 } else { 2 },
        needs_base,
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

/// Bring the runtime up to what this build needs.
///
/// Two halves, fetched independently. The usual case after the first install
/// is the app alone: two hundred kilobytes and a hundred and thirty files,
/// against four hundred megabytes and twenty thousand. The heavy half is only
/// touched when its digest changed, which happens when a dependency moved, not
/// when a release happened.
pub fn install(
    app_data: &Path,
    version: &str,
    mut on_progress: impl FnMut(Phase, u64, u64),
) -> Result<()> {
    let _ = LOG.set(app_data.join("runtime-install.log"));
    let state = status(app_data, version);

    // Kept between attempts on purpose: this is where a half-finished download
    // waits to be resumed.
    let scratch = app_data.join("runtime.download");
    fs::create_dir_all(&scratch).context("无法创建下载目录")?;

    if state.needs_base {
        let base = BASE_VERSION.trim();
        fetch_part(
            &base_url(base),
            &scratch,
            "base.tar.gz",
            &format!("取不到 base 的校验和（{}，{}）", target(), BASE_VERSION.trim()),
            &mut on_progress,
        )?;

        let unpacked = scratch.join("runtime");
        if !unpacked.join("python").exists() {
            let _ = fs::remove_dir_all(&scratch);
            bail!("base 包结构不对：缺少 python");
        }
        swap_in(app_data, &unpacked)?;
    }

    // The app half unpacks over the live tree rather than beside it. It cannot
    // be swapped like the base: it is a handful of files inside a directory
    // whose other four hundred megabytes must stay exactly where they are.
    let live = app_data.join("runtime");
    fetch_part(
        &app_url(version),
        app_data,
        "app.tar.gz",
        &format!("取不到 app 的校验和（{version}）"),
        &mut on_progress,
    )?;
    if !live.join("runtime.json").exists() {
        bail!("运行时包结构不对：缺少 runtime.json");
    }

    let _ = fs::remove_dir_all(&scratch);
    let _ = fs::remove_file(app_data.join("app.tar.gz"));
    Ok(())
}

/// Download one half, check it, unpack it where told.
fn fetch_part(
    base: &str,
    into: &Path,
    name: &str,
    missing: &str,
    on_progress: &mut impl FnMut(Phase, u64, u64),
) -> Result<()> {
    let expected = match fetch_checksum(&format!("{base}.sha256")) {
        Ok(value) => value,
        Err(error) => {
            // Written down as well as returned: a dialog gets dismissed, and
            // this is the line that says which URL and which failure.
            log(&format!("取校验和失败：{base}.sha256 → {error:#}"));
            return Err(error).context(missing.to_string());
        }
    };
    let archive = into.join(name);
    let digest = fetch_with_resume(&format!("{base}.tar.gz"), &archive, &mut |done, total| {
        on_progress(Phase::Download, done, total)
    })?;
    if digest != expected {
        // Only now is the partial file worthless: keeping a file whose bytes we
        // know are wrong would make every future attempt resume from garbage.
        let _ = fs::remove_file(&archive);
        bail!("下载的运行时校验失败，已清掉重来；再试一次通常就好了");
    }
    unpack(&archive, into, |done, total| on_progress(Phase::Unpack, done, total))
}

/// Put a freshly unpacked tree in place of the live one, last of all, so that
/// until this moment the previous runtime is the one still working.
fn swap_in(app_data: &Path, unpacked: &Path) -> Result<()> {
    let live = app_data.join("runtime");
    let retired = app_data.join("runtime.old");
    let _ = fs::remove_dir_all(&retired);
    if live.exists() {
        fs::rename(&live, &retired).context("无法移开旧的运行时")?;
    }
    fs::rename(unpacked, &live).context("无法启用新的运行时")?;
    let _ = fs::remove_dir_all(&retired);
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
    let mut builder = ureq::AgentBuilder::new()
        .timeout_connect(std::time::Duration::from_secs(30))
        .timeout_read(std::time::Duration::from_secs(60));

    // The browser on the same machine did 419MB in forty seconds; this client
    // managed 0.10MB/s. The difference was not the code — it was that a
    // browser uses the machine's proxy and `ureq` does not, so every request
    // here went out direct on a route that barely worked, and kept being cut.
    if let Some(url) = system_proxy() {
        match ureq::Proxy::new(&url) {
            Ok(proxy) => {
                log(&format!("走代理：{url}"));
                builder = builder.proxy(proxy);
            }
            // A proxy we cannot parse is worth saying out loud rather than
            // silently ignoring — silently ignoring is what put us here.
            Err(error) => log(&format!("代理地址无法解析，改为直连：{url}（{error}）")),
        }
    }
    builder.build()
}

/// What this machine says its proxy is, in the order the machine means it.
///
/// The environment wins: someone who exports `HTTPS_PROXY` for a single run
/// means that run. Otherwise Windows' own setting — the one the browser obeys,
/// which is exactly the discrepancy this exists to close.
pub fn system_proxy() -> Option<String> {
    for name in ["HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"]
    {
        if let Ok(value) = std::env::var(name) {
            let value = value.trim();
            if !value.is_empty() {
                return Some(value.to_string());
            }
        }
    }
    windows_proxy()
}

#[cfg(windows)]
fn windows_proxy() -> Option<String> {
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;

    let settings = RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey(r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        .ok()?;
    let enabled: u32 = settings.get_value("ProxyEnable").ok()?;
    if enabled == 0 {
        return None;
    }
    let server: String = settings.get_value("ProxyServer").ok()?;

    // Either "host:port" for everything, or "http=…;https=…;socks=…" per
    // scheme. We want the one that carries HTTPS.
    let server = server.trim();
    if !server.contains('=') {
        return Some(with_scheme(server));
    }
    for part in server.split(';') {
        if let Some(rest) = part.trim().strip_prefix("https=") {
            return Some(with_scheme(rest));
        }
    }
    for part in server.split(';') {
        if let Some(rest) = part.trim().strip_prefix("http=") {
            return Some(with_scheme(rest));
        }
    }
    None
}

#[cfg(not(windows))]
fn windows_proxy() -> Option<String> {
    None
}

fn with_scheme(address: &str) -> String {
    if address.contains("://") {
        address.to_string()
    } else {
        format!("http://{address}")
    }
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
        let before = fs::metadata(to).map(|m| m.len()).unwrap_or(0);
        match append_from(url, to, on_progress) {
            Ok(()) => {
                log(&format!("下完了：{url} 共 {} 字节", fs::metadata(to).map(|m| m.len()).unwrap_or(0)));
                return hash_file(to);
            }
            Err(error) => {
                // A connection that dropped mid-stream leaves bytes worth
                // keeping; back off a little and ask for the rest.
                //
                // The byte counts are the point of this line. Without them a
                // log of six identical "中断" tells you nothing about whether
                // the resume is working — whether each attempt is inching
                // forward or whether every one of them starts from zero, which
                // are different bugs with different fixes.
                let after = fs::metadata(to).map(|m| m.len()).unwrap_or(0);
                log(&format!(
                    "第 {} 次尝试中断（{}），这次从 {} 下到 {}（+{}），{} 秒后继续",
                    attempt + 1,
                    error,
                    before,
                    after,
                    after.saturating_sub(before),
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

/// How much has to arrive before the window is told again.
///
/// A TLS record is about 8KB, so `read` returns that much at a time and a
/// 419MB download is fifty thousand of them. Reporting each one sends fifty
/// thousand events across the IPC boundary, and every one of those is a
/// serialize, a hop into the webview and a React render — measured against a
/// browser doing the same download in forty seconds, the client had managed
/// one percent. The bar does not need more than a few hundred updates.
const REPORT_EVERY: u64 = 2 << 20;

/// …but never let this long pass in silence.
///
/// Bytes alone are the wrong clock for the people this matters most to: on a
/// link doing 100KB/s, two megabytes is twenty seconds of a bar that appears
/// frozen — which is exactly the impression we are trying to stop giving.
const REPORT_AT_LEAST_EVERY: std::time::Duration = std::time::Duration::from_millis(500);

/// One attempt: ask for everything after what is already on disk.
fn append_from(url: &str, to: &Path, on_progress: &mut impl FnMut(u64, u64)) -> Result<()> {
    let have = fs::metadata(to).map(|m| m.len()).unwrap_or(0);

    let mut request = http_agent().get(url);
    if have > 0 {
        request = request.set("Range", &format!("bytes={have}-"));
    }

    let response = request.call().context("下载失败")?;
    let resuming = response.status() == 206;
    if resuming {
        // Worth a line: a bar that opens at 6% looks like a download that went
        // very fast and then stalled, when nothing was downloaded at all —
        // those bytes were left by an earlier attempt.
        log(&format!("从上次的 {have} 字节继续：{url}"));
    }
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

    // Buffered for the same reason: fifty thousand 8KB writes are fifty
    // thousand syscalls, and on Windows each one passes under the antivirus.
    let mut file = std::io::BufWriter::with_capacity(1 << 20, file);

    let mut buffer = vec![0u8; 1 << 20];
    let mut done = have;
    let mut reported = have;
    let mut reported_at = std::time::Instant::now();
    // Kept rather than propagated at once: a dropped connection is the case
    // this whole function exists for, and the bytes it did deliver still have
    // to be flushed and still have to be reported. Letting `?` out of the loop
    // threw both away — the window froze at whatever the last throttled report
    // had said, and the retry then appeared to start from there.
    let mut interrupted: Option<std::io::Error> = None;
    loop {
        let read = match source.read(&mut buffer) {
            Ok(0) => break,
            Ok(count) => count,
            Err(error) => {
                interrupted = Some(error);
                break;
            }
        };
        std::io::Write::write_all(&mut file, &buffer[..read])?;
        done += read as u64;
        if done - reported >= REPORT_EVERY || reported_at.elapsed() >= REPORT_AT_LEAST_EVERY {
            reported = done;
            reported_at = std::time::Instant::now();
            on_progress(done, total);
        }
    }
    std::io::Write::flush(&mut file)?;
    on_progress(done, total);

    if let Some(error) = interrupted {
        // Reported as bytes rather than as an io error: what the next attempt
        // needs to know is where to resume from.
        bail!("连接中断（{error}）：已下 {done} 字节");
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

/// How many files the runtime holds, near enough to draw a bar with.
///
/// The exact count is only knowable by reading the archive twice, and the
/// second read costs as much as the unpack. An estimate that is wrong by a few
/// percent still tells someone the thing is moving, which is the entire point.
const APPROX_ENTRIES: u64 = 21_000;

fn unpack(archive: &Path, into: &Path, mut on_progress: impl FnMut(u64, u64)) -> Result<()> {
    let file = fs::File::open(archive)?;
    // Buffered on purpose. The decoder otherwise reads the file in small
    // chunks, one syscall each, and on Windows syscalls are expensive enough
    // that this alone dominated the unpack of a 21,000-file tree.
    let buffered = std::io::BufReader::with_capacity(1 << 20, file);
    let decoder = flate2::read::GzDecoder::new(buffered);
    let mut tar = tar::Archive::new(decoder);
    // Preserve the executable bits: without them the interpreter unpacks as a
    // file the system will not run.
    tar.set_preserve_permissions(true);

    // Entry by entry rather than `unpack()`, only so there is something to
    // report. Unpacking is minutes of work on a machine whose antivirus
    // inspects every one of those files, and it used to happen behind a
    // progress bar frozen at 100% — indistinguishable from a hang, and the
    // reason "安装要一两个小时" was as much about silence as about time.
    let mut done = 0u64;
    for entry in tar.entries().context("解压失败")? {
        let mut entry = entry.context("解压失败")?;
        entry.unpack_in(into).context("解压失败")?;
        done += 1;
        // Reporting each file would be twenty thousand IPC messages.
        if done % 200 == 0 {
            on_progress(done, APPROX_ENTRIES);
        }
    }
    on_progress(APPROX_ENTRIES, APPROX_ENTRIES);
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
    fn an_exported_proxy_beats_whatever_the_machine_is_configured_with() {
        // Someone exporting HTTPS_PROXY for one run means that run.
        std::env::set_var("HTTPS_PROXY", "http://127.0.0.1:7890");
        assert_eq!(system_proxy().as_deref(), Some("http://127.0.0.1:7890"));
        std::env::remove_var("HTTPS_PROXY");
    }

    #[test]
    fn a_bare_host_and_port_is_given_a_scheme() {
        // Windows stores "127.0.0.1:7890"; ureq needs a URL.
        assert_eq!(with_scheme("127.0.0.1:7890"), "http://127.0.0.1:7890");
        assert_eq!(with_scheme("socks5://127.0.0.1:7891"), "socks5://127.0.0.1:7891");
    }

    #[test]
    fn an_empty_proxy_variable_is_not_a_proxy() {
        // Set-but-empty is how a shell says "no proxy"; treating it as one
        // would send every request at a host that is not there.
        std::env::set_var("HTTPS_PROXY", "   ");
        let found = system_proxy();
        std::env::remove_var("HTTPS_PROXY");
        assert!(found.is_none() || !found.unwrap().trim().is_empty());
    }

    #[test]
    fn the_app_url_names_the_release_and_the_platform() {
        let url = app_url("1.2.3");
        assert!(url.contains("/v1.2.3/"), "{url}");
        assert!(url.ends_with(&format!("d2v-app-1.2.3-{}", target())), "{url}");
    }

    #[test]
    fn the_base_url_names_the_digest_rather_than_a_release() {
        // Deliberately not a version: two releases that did not move a
        // dependency must resolve to the same file, so that the second one
        // costs nobody a download.
        let url = base_url("abc123");
        assert!(url.contains("/runtime-base-abc123/"), "{url}");
        assert!(url.ends_with(&format!("d2v-base-abc123-{}", target())), "{url}");
    }

    #[test]
    fn a_runtime_from_before_the_split_is_replaced_rather_than_trusted() {
        // Its manifest has no `base`, and nothing in it says which
        // dependencies it holds. Reusing it would be a guess.
        let dir = std::env::temp_dir().join("d2v-test-legacy");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("runtime")).unwrap();
        fs::write(
            dir.join("runtime").join("runtime.json"),
            r#"{"version":"0.7.2","target":"x"}"#,
        )
        .unwrap();

        let state = status(&dir, "0.7.2");
        assert!(state.needs_base, "旧运行时没有 base，不能当成可用的");
        assert!(!state.ready);
        assert_eq!(state.approx_mb, 400);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_new_release_on_an_unchanged_base_only_needs_the_small_half() {
        let dir = std::env::temp_dir().join("d2v-test-apponly");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("runtime")).unwrap();
        fs::write(
            dir.join("runtime").join("runtime.json"),
            format!(
                r#"{{"version":"0.7.2","base":"{}","app":"0.7.2"}}"#,
                BASE_VERSION.trim()
            ),
        )
        .unwrap();

        let state = status(&dir, "0.7.3");
        assert!(!state.needs_base, "依赖没动，不该再拉一遍 400MB");
        assert!(!state.ready, "app 版本对不上，还是要装");
        assert_eq!(state.approx_mb, 2);
        let _ = fs::remove_dir_all(&dir);
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
        // The interrupted attempt must have reported what it did manage —
        // otherwise a stalled download looks frozen at the last throttled
        // number rather than at the point it actually reached.
        assert!(
            seen.iter().any(|&d| d > 0 && d < body.len() as u64),
            "中断时的进度没有上报：{seen:?}"
        );

        drop(server);
        let _ = fs::remove_dir_all(&dir);
    }
}

#[cfg(test)]
mod throughput_tests {
    use super::*;

    /// A real download must not flood the window with progress events.
    ///
    /// This is the whole of the bug it guards: `read` returns one TLS record —
    /// about 8KB — so reporting every read meant fifty thousand IPC hops for a
    /// 419MB file, and the download crawled at a fiftieth of what the same
    /// machine's browser managed.
    #[test]
    #[ignore]
    fn a_download_reports_progress_a_few_hundred_times_not_fifty_thousand() {
        let url = format!(
            "{REPO}/releases/download/runtime-base-ddb04e343cce/\
             d2v-base-ddb04e343cce-macos-arm64.tar.gz"
        );
        let to = std::env::temp_dir().join("d2v-progress-test.bin");
        let _ = fs::remove_file(&to);

        let mut calls = 0u64;
        let started = std::time::Instant::now();
        // Twenty megabytes is enough to show the ratio without fetching 419.
        let _ = append_from(&url, &to, &mut |_done, _total| calls += 1);
        let seconds = started.elapsed().as_secs_f64();
        let size = fs::metadata(&to).map(|m| m.len()).unwrap_or(0);
        println!(
            "{:.0}MB / {:.1}s = {:.1}MB/s，回调 {} 次",
            size as f64 / 1e6,
            seconds,
            size as f64 / 1e6 / seconds,
            calls
        );

        // One per 2MB or per half-second, whichever comes first, plus the
        // final one — so the bound is against the slower of the two clocks.
        let ceiling = size / REPORT_EVERY + (seconds * 2.0) as u64 + 2;
        assert!(calls <= ceiling, "回调 {calls} 次，上限 {ceiling}");
        let _ = fs::remove_file(&to);
    }

    /// How fast the HTTP layer alone can pull bytes, and in what size pieces.
    ///
    /// Run deliberately — it downloads twenty megabytes:
    ///     cargo test -- --ignored --nocapture throughput
    #[test]
    #[ignore]
    fn throughput() {
        let url = format!("{REPO}/releases/download/runtime-base-ddb04e343cce/d2v-base-ddb04e343cce-macos-arm64.tar.gz");
        let response = http_agent()
            .get(&url)
            .set("Range", "bytes=0-20000000")
            .call()
            .expect("请求失败");
        let mut source = response.into_reader();

        let mut buffer = vec![0u8; 1 << 20];
        let (mut done, mut reads) = (0u64, 0u64);
        let started = std::time::Instant::now();
        loop {
            let read = source.read(&mut buffer).expect("读取失败");
            if read == 0 {
                break;
            }
            done += read as u64;
            reads += 1;
        }
        let seconds = started.elapsed().as_secs_f64();
        println!(
            "纯 ureq：{:.1}MB / {:.1}s = {:.1}MB/s，{} 次 read，平均每次 {:.0}KB",
            done as f64 / 1e6,
            seconds,
            done as f64 / 1e6 / seconds,
            reads,
            done as f64 / reads as f64 / 1024.0,
        );
    }
}
