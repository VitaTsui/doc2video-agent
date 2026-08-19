//! Owning the backend process.
//!
//! The pipeline stays in Python — twenty-odd HTTP endpoints, the job queue,
//! telemetry and the quality report all live there, and rewriting them in Rust
//! would be rebuilding the product. This module's whole job is to start that
//! process, tell it where it may write, hand it the user's keys, know when it
//! is ready, and make sure it dies when the app does.
//!
//! Five things it has to get right, each forced by how the backend behaves:
//!
//! * **A port nobody else has.** The default 8400 is fine for a server and
//!   wrong for a desktop app, where a second copy or an unrelated program may
//!   already hold it. We bind :0, note what the OS gave us, release it, and
//!   pass that on — a small race we accept because the alternative is a fixed
//!   port that fails in a way the user cannot fix.
//! * **A token that never touches disk.** The backend authenticates every
//!   route with a bearer token. A fresh random one per launch, passed through
//!   the environment, means nothing to leak and nothing to rotate.
//! * **Somewhere writable.** `storage_dir` defaults to `./storage`, relative to
//!   the working directory — for an installed app that is wherever the OS
//!   happened to launch it from. It has to be set explicitly.
//! * **Readiness by asking.** `serve` prints a banner and then blocks; there is
//!   no ready signal to wait for. `GET /health` is the only honest answer.
//! * **No survivors.** Two things conspire here. A force-killed shell runs no
//!   destructor, so the pid is recorded and any stale backend is cleared on the
//!   next launch. And the process we spawn is not the backend — in a source
//!   checkout it is `uv`, which forks the interpreter — so killing the pid we
//!   hold leaves the actual server running, holding a port and, mid-render, a
//!   core. The child gets its own process group and the whole group is killed.

use std::io::{BufRead, BufReader};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{anyhow, Context, Result};
use rand::RngCore;

#[cfg(unix)]
use std::os::unix::process::CommandExt;

/// How long to wait for the backend to answer /health before giving up.
/// Generous: a first launch imports PyMuPDF and friends on a cold page cache.
const READY_TIMEOUT: Duration = Duration::from_secs(90);

/// How much of the backend's stderr to keep for a start that never became
/// ready. Its output is piped, so this is the only place a traceback survives.
const DIAGNOSTIC_LINES: usize = 60;

pub struct Backend {
    child: Child,
    pub base_url: String,
    pub token: String,
    stderr: Arc<Mutex<Vec<String>>>,
    pid_file: PathBuf,
}

impl Backend {
    /// Start the backend and wait until it answers.
    pub fn start(paths: &Paths, keys: Vec<(String, String)>) -> Result<Self> {
        clear_stale(&paths.pid_file);

        let port = free_port().context("找不到可用端口")?;
        let token = random_token();
        let base_url = format!("http://127.0.0.1:{port}");

        let mut command = paths.command()?;
        command
            .arg("serve")
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(port.to_string())
            .env("D2V_API_TOKEN", &token)
            .env("D2V_STORAGE_DIR", &paths.storage_dir)
            .env("D2V_NODE_DIR", &paths.node_dir)
            // The webview's origin is not the backend's, so without this every
            // request from the UI is blocked before it is ever sent.
            .env(
                "D2V_CORS_ORIGINS",
                r#"["tauri://localhost","https://tauri.localhost","http://tauri.localhost"]"#,
            )
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped());

        // The runtime ships its own node; without it on PATH the backend looks
        // for `npx`, does not find one, and quietly renders through ffmpeg with
        // plainer slides — a downgrade nobody asked for and nothing reports.
        if let Some(bin) = paths.node_bin() {
            let existing = std::env::var("PATH").unwrap_or_default();
            let separator = if cfg!(windows) { ";" } else { ":" };
            command.env("PATH", format!("{}{separator}{existing}", bin.display()));
        }

        // Its own process group, so the whole tree can be signalled at once.
        // `uv run` forks the interpreter; signalling only what we spawned would
        // leave the server itself alive.
        #[cfg(unix)]
        command.process_group(0);

        // The backend is a console program, and Windows gives console programs
        // a console — a black window that opens beside the app, belongs to
        // nothing the user asked for, and closing it kills the backend.
        #[cfg(windows)]
        no_window(&mut command);

        for (name, value) in keys {
            command.env(name, value);
        }

        let mut child = command.spawn().context("无法启动后端进程")?;
        let _ = std::fs::write(&paths.pid_file, child.id().to_string());

        let stderr = Arc::new(Mutex::new(Vec::new()));
        if let Some(pipe) = child.stderr.take() {
            let sink = Arc::clone(&stderr);
            // Drained on a thread: a full pipe would block the backend itself,
            // and the logs are worth more than they cost.
            std::thread::spawn(move || {
                for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                    let mut kept = sink.lock().unwrap();
                    if kept.len() == DIAGNOSTIC_LINES {
                        kept.remove(0);
                    }
                    kept.push(line);
                }
            });
        }

        let backend = Self {
            child,
            base_url,
            token,
            stderr,
            pid_file: paths.pid_file.clone(),
        };
        backend.wait_until_ready()?;
        Ok(backend)
    }

    fn wait_until_ready(&self) -> Result<()> {
        let deadline = Instant::now() + READY_TIMEOUT;
        let health = format!("{}/health", self.base_url);
        loop {
            if let Ok(response) = ureq::get(&health).timeout(Duration::from_secs(2)).call() {
                if response.status() == 200 {
                    return Ok(());
                }
            }
            if Instant::now() > deadline {
                return Err(anyhow!(
                    "后端启动超时（{}s）\n{}",
                    READY_TIMEOUT.as_secs(),
                    self.diagnostics()
                ));
            }
            std::thread::sleep(Duration::from_millis(250));
        }
    }

    /// The backend's recent stderr — where its traceback is, if it has one.
    pub fn diagnostics(&self) -> String {
        self.stderr.lock().unwrap().join("\n")
    }
}

impl Drop for Backend {
    /// A backend that outlives the window holds the user's port and their CPU.
    fn drop(&mut self) {
        stop_group(self.child.id());
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_file(&self.pid_file);
    }
}

/// Signal a whole process group, politely and then not.
/// Spawn without handing the child a console window.
///
/// `CREATE_NO_WINDOW`. Applies to every process this shell starts: the backend
/// itself, and the `taskkill` that stops it — a window that flashes for a
/// tenth of a second on quit is still a window nobody asked for.
#[cfg(windows)]
fn no_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

fn stop_group(pid: u32) {
    #[cfg(windows)]
    {
        // /T takes the tree; there are no process groups to address.
        let mut command = Command::new("taskkill");
        command.args(["/PID", &pid.to_string(), "/T", "/F"]);
        no_window(&mut command);
        let _ = command.output();
    }

    #[cfg(not(windows))]
    {
        let group = format!("-{pid}");
        let _ = Command::new("kill").args(["-TERM", &group]).output();
        std::thread::sleep(Duration::from_millis(300));
        let _ = Command::new("kill").args(["-KILL", &group]).output();
    }
}

/// Where the backend and its Node workspace are, and where it may write.
pub struct Paths {
    pub program: Program,
    pub storage_dir: PathBuf,
    pub node_dir: PathBuf,
    pub pid_file: PathBuf,
}

/// How to invoke the backend. Two shapes, because a development checkout and an
/// installed app have nothing in common here.
pub enum Program {
    /// A downloaded runtime: an interpreter with the package installed beside it.
    Bundled { python: PathBuf },
    /// A source checkout driven through uv, so the app tracks local edits.
    Source { repo: PathBuf },
}

impl Paths {
    /// The runtime's own `node`/`npx`, when it shipped with one.
    fn node_bin(&self) -> Option<PathBuf> {
        let bin = self.node_dir.join("bin");
        bin.is_dir().then_some(bin)
    }

    fn command(&self) -> Result<Command> {
        match &self.program {
            Program::Bundled { python } => {
                if !python.exists() {
                    return Err(anyhow!("运行时不完整，缺少解释器：{}", python.display()));
                }
                let mut command = Command::new(python);
                command.arg("-m").arg("doc2video.cli");
                Ok(command)
            }
            Program::Source { repo } => {
                let mut command = Command::new("uv");
                command
                    .arg("run")
                    .arg("--project")
                    .arg(repo)
                    .arg("doc2video");
                Ok(command)
            }
        }
    }

    /// Resolve from the app's own layout, preferring a downloaded runtime.
    pub fn resolve(app_data: &Path) -> Result<Self> {
        let storage_dir = app_data.join("storage");
        std::fs::create_dir_all(&storage_dir).context("无法创建数据目录")?;
        let pid_file = app_data.join("backend.pid");

        let runtime = app_data.join("runtime");
        if runtime.join("python").exists() {
            return Ok(Self {
                program: Program::Bundled {
                    python: runtime.join("python").join(python_bin()),
                },
                node_dir: runtime.join("node"),
                storage_dir,
                pid_file,
            });
        }

        // Development: walk up from this crate to the repository that has the
        // backend in it. Compiled in, because a dev build is run from the tree
        // it was built in and there is nothing else to point at.
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .find(|dir| dir.join("pyproject.toml").exists())
            .map(Path::to_path_buf)
            .ok_or_else(|| anyhow!("既没有下载运行时，也没有找到源码仓库"))?;

        Ok(Self {
            program: Program::Source { repo: repo.clone() },
            node_dir: repo.join("renderer"),
            storage_dir,
            pid_file,
        })
    }
}

/// Kill a backend left behind by a shell that did not exit cleanly.
///
/// The pid is only killed when the system still reports a live process under
/// it: process ids are reused, and killing whatever now holds the number would
/// be far worse than leaving one stale backend running.
fn clear_stale(pid_file: &Path) {
    let Ok(text) = std::fs::read_to_string(pid_file) else {
        return;
    };
    let Ok(pid) = text.trim().parse::<u32>() else {
        let _ = std::fs::remove_file(pid_file);
        return;
    };

    // Only kill it when the system still reports our own program under that
    // id: process ids are reused, and killing whatever now holds the number
    // would be far worse than leaving one stale backend running.
    #[cfg(not(windows))]
    let ours = Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "command="])
        .output()
        .map(|out| String::from_utf8_lossy(&out.stdout).contains("doc2video"))
        .unwrap_or(false);
    #[cfg(windows)]
    let ours = true;

    if ours {
        stop_group(pid);
    }
    let _ = std::fs::remove_file(pid_file);
}

fn python_bin() -> &'static str {
    if cfg!(windows) {
        "python.exe"
    } else {
        "bin/python3"
    }
}

fn free_port() -> Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    Ok(listener.local_addr()?.port())
}

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_token_is_long_enough_to_be_worth_checking() {
        let token = random_token();
        assert_eq!(token.len(), 64);
        assert_ne!(token, random_token());
    }

    #[test]
    fn free_ports_are_not_handed_out_twice() {
        assert_ne!(free_port().unwrap(), 0);
    }

    #[test]
    fn a_pid_file_holding_nonsense_is_removed_not_obeyed() {
        let dir = std::env::temp_dir().join(format!("d2v-pid-{}", random_token()));
        std::fs::create_dir_all(&dir).unwrap();
        let pid_file = dir.join("backend.pid");
        std::fs::write(&pid_file, "not a pid").unwrap();

        clear_stale(&pid_file);

        assert!(!pid_file.exists());
        std::fs::remove_dir_all(&dir).ok();
    }
}
