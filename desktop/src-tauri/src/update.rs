//! Checking for a newer shell, and replacing this one with it.
//!
//! Only the shell updates here — the window, the supervisor, the key store.
//! The runtime is versioned and fetched separately (`runtime.rs`), because it
//! is four hundred megabytes and the shell is fifteen.
//!
//! Nothing installs on its own. An update arrives while a render may be in
//! flight, and installing means restarting the process that owns the backend —
//! which would take a job's worth of minutes with it. So the check is silent
//! and the install is a decision the person makes, at a moment they choose.

use serde::Serialize;
use tauri::AppHandle;
use tauri_plugin_updater::UpdaterExt;

#[derive(Serialize, Default)]
pub struct Available {
    pub available: bool,
    /// Empty when there is nothing newer.
    pub version: String,
    pub notes: String,
    pub current: String,
}

#[tauri::command]
pub async fn check_update(app: AppHandle) -> Result<Available, String> {
    let current = app.package_info().version.to_string();
    // A failed check is not a failed application: no network, a rate limit, or
    // a release without a manifest all mean "not now", not "something broke".
    let updater = app.updater().map_err(|e| format!("检查更新失败：{e}"))?;
    let found = updater
        .check()
        .await
        .map_err(|e| format!("检查更新失败：{e}"))?;

    Ok(match found {
        Some(update) => Available {
            available: true,
            version: update.version.clone(),
            notes: update.body.clone().unwrap_or_default(),
            current,
        },
        None => Available {
            available: false,
            current,
            ..Default::default()
        },
    })
}

/// Download, replace, and restart into the new version.
///
/// The restart is the point — a shell that downloaded an update and kept
/// running the old binary has done nothing — but it also kills the backend,
/// which is why the window refuses to call this while a job is running.
#[tauri::command]
pub async fn install_update(app: AppHandle) -> Result<(), String> {
    let updater = app.updater().map_err(|e| format!("检查更新失败：{e}"))?;
    let update = updater
        .check()
        .await
        .map_err(|e| format!("检查更新失败：{e}"))?
        .ok_or_else(|| "已经是最新版本了".to_string())?;

    update
        .download_and_install(|_chunk, _total| {}, || {})
        .await
        .map_err(|e| format!("更新失败：{e}"))?;

    app.restart();
}
