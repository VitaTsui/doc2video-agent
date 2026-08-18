//! Doc2Video desktop shell.
//!
//! Carries no product logic. It starts the backend, tells the window where to
//! find it, keeps the user's keys out of reach of the page, and stops
//! everything on the way out. The UI talks to the backend over ordinary HTTP —
//! the same API an MCP client or a curl script would use — so nothing here is
//! a private channel that only the desktop build knows about.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod prefs;
mod runtime;
mod secrets;
mod sidecar;

use std::sync::Mutex;

use serde::Serialize;
use tauri::{Emitter, Manager, State};

use prefs::Prefs;
use sidecar::{Backend, Paths};

/// What the window needs to talk to the backend.
#[derive(Serialize, Clone)]
struct Connection {
    base_url: String,
    token: String,
}

#[derive(Default)]
struct AppState {
    backend: Mutex<Option<Backend>>,
}

/// Where the backend is and how to authenticate to it.
///
/// The token is handed to the page deliberately: it is a per-launch secret for
/// a loopback server the page is the only client of, and the alternative —
/// proxying every call through Rust — would mean reimplementing the API here.
#[tauri::command]
fn connection(state: State<'_, AppState>) -> Result<Connection, String> {
    let guard = state.backend.lock().map_err(|_| "状态锁损坏")?;
    let backend = guard.as_ref().ok_or("后端尚未启动")?;
    Ok(Connection {
        base_url: backend.base_url.clone(),
        token: backend.token.clone(),
    })
}

#[tauri::command]
fn configured_keys() -> Vec<String> {
    secrets::configured()
}

/// Store a key and restart the backend so it takes effect.
#[tauri::command]
fn save_key(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    vendor: String,
    key: String,
) -> Result<Connection, String> {
    if !secrets::VENDORS.contains(&vendor.as_str()) {
        return Err(format!("未知的厂商：{vendor}"));
    }
    secrets::store(&vendor, &key).map_err(|e| format!("无法写入钥匙串：{e}"))?;
    restart(&app, &state)
}

#[tauri::command]
fn restart_backend(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<Connection, String> {
    restart(&app, &state)
}

/// Whether the runtime this build needs is installed.
#[tauri::command]
fn runtime_status(app: tauri::AppHandle) -> Result<runtime::Status, String> {
    Ok(runtime::status(&app_data(&app)?, app.package_info().version.to_string().as_str()))
}

/// Download and install the runtime, then start the backend.
///
/// Runs on a blocking thread and reports progress as events: four hundred
/// megabytes with no sign of movement is indistinguishable from a hang, and
/// this is the first thing a new user sees.
#[tauri::command]
async fn install_runtime(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Connection, String> {
    let dir = app_data(&app)?;
    let version = app.package_info().version.to_string();
    let emitter = app.clone();

    tauri::async_runtime::spawn_blocking(move || {
        runtime::install(&dir, &version, |done, total| {
            let _ = emitter.emit("runtime://progress", (done, total));
        })
    })
    .await
    .map_err(|e| format!("安装被打断：{e}"))?
    .map_err(|e| e.to_string())?;

    restart(&app, &state)
}

/// The model this app is configured to use, as last chosen.
#[tauri::command]
fn model_prefs(app: tauri::AppHandle) -> Result<Prefs, String> {
    Ok(prefs::load(&app_data(&app)?))
}

/// Choose a model, and restart the backend so it takes effect.
#[tauri::command]
fn save_model_prefs(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    prefs: Prefs,
) -> Result<Connection, String> {
    prefs::save(&app_data(&app)?, &prefs)?;
    restart(&app, &state)
}

fn app_data(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("找不到数据目录：{e}"))?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("无法创建数据目录：{e}"))?;
    Ok(dir)
}

fn restart(app: &tauri::AppHandle, state: &State<'_, AppState>) -> Result<Connection, String> {
    let mut guard = state.backend.lock().map_err(|_| "状态锁损坏")?;
    // Dropped before the new one starts: two backends would race for the same
    // storage directory, and the old one is holding a port we no longer track.
    *guard = None;

    let backend = start_backend(app)?;
    let connection = Connection {
        base_url: backend.base_url.clone(),
        token: backend.token.clone(),
    };
    *guard = Some(backend);
    Ok(connection)
}

fn start_backend(app: &tauri::AppHandle) -> Result<Backend, String> {
    let dir = app_data(app)?;
    let paths = Paths::resolve(&dir).map_err(|e| e.to_string())?;

    // Keys from the keychain, the model choice from disk — both only reach the
    // backend as environment variables at spawn, which is why changing either
    // means starting a new one.
    let mut env = secrets::as_env();
    env.extend(prefs::load(&dir).as_env());
    Backend::start(&paths, env).map_err(|e| e.to_string())
}

/// Stop the backend when the process is signalled.
///
/// Closing the window runs `Drop`; `kill` does not, and neither does anything
/// that takes the process down without unwinding. The next launch would clear
/// the orphan, but "next launch" can be hours of a spinning fan away.
#[cfg(unix)]
fn stop_on_signal(app: &tauri::AppHandle) {
    use signal_hook::consts::{SIGINT, SIGTERM};
    use signal_hook::iterator::Signals;

    let handle = app.clone();
    let Ok(mut signals) = Signals::new([SIGTERM, SIGINT]) else {
        return;
    };
    std::thread::spawn(move || {
        if signals.forever().next().is_some() {
            if let Some(state) = handle.try_state::<AppState>() {
                if let Ok(mut guard) = state.backend.lock() {
                    *guard = None; // Drop stops the whole process group.
                }
            }
            std::process::exit(0);
        }
    });
}

#[cfg(not(unix))]
fn stop_on_signal(_app: &tauri::AppHandle) {}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            connection,
            configured_keys,
            runtime_status,
            install_runtime,
            save_key,
            model_prefs,
            save_model_prefs,
            restart_backend
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            stop_on_signal(&handle);

            // With no runtime there is nothing to start; the window asks to
            // download one. Starting the backend here otherwise, rather than
            // lazily, means a failure is reported at launch instead of halfway
            // through someone's first render.
            let dir = app_data(&handle).map_err(std::io::Error::other)?;
            let version = handle.package_info().version.to_string();
            if runtime::status(&dir, &version).ready {
                let backend = start_backend(&handle)?;
                app.state::<AppState>()
                    .backend
                    .lock()
                    .unwrap()
                    .replace(backend);
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("桌面壳启动失败");
}
