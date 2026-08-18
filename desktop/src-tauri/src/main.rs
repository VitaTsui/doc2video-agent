//! Doc2Video desktop shell.
//!
//! Carries no product logic. It starts the backend, tells the window where to
//! find it, keeps the user's keys out of reach of the page, and stops
//! everything on the way out. The UI talks to the backend over ordinary HTTP —
//! the same API an MCP client or a curl script would use — so nothing here is
//! a private channel that only the desktop build knows about.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod secrets;
mod sidecar;

use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, State};

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
    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("找不到数据目录：{e}"))?;
    std::fs::create_dir_all(&app_data).map_err(|e| format!("无法创建数据目录：{e}"))?;

    let paths = Paths::resolve(&app_data).map_err(|e| e.to_string())?;
    Backend::start(&paths, secrets::as_env()).map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            connection,
            configured_keys,
            save_key,
            restart_backend
        ])
        .setup(|app| {
            // Started here rather than lazily on first use: the window's very
            // first action is to ask where the backend is, and a failure at
            // launch is far easier to explain than one halfway through a render.
            let handle = app.handle().clone();
            let backend = start_backend(&handle)?;
            app.state::<AppState>()
                .backend
                .lock()
                .unwrap()
                .replace(backend);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("桌面壳启动失败");
}
