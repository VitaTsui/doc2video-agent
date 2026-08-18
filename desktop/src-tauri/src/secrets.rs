//! Model API keys, in the OS credential store.
//!
//! Keys never reach a config file. They are read out of the keychain at launch
//! and handed to the backend as environment variables, which is also why
//! changing one restarts the backend rather than being applied live: the
//! backend caches its settings for the life of the process (`get_settings` is
//! an `lru_cache`), so a restart is the only honest way to apply a change.
//!
//! The vendor names match what the backend reads, so nothing has to translate
//! between the two — see `doc2video/core/config.py`.

use anyhow::Result;
use keyring::Entry;

const SERVICE: &str = "com.vitahsu.doc2video";

/// The environment variable each stored key becomes.
pub const VENDORS: [&str; 4] = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "D2V_COMPATIBLE_API_KEY",
];

pub fn store(vendor: &str, key: &str) -> Result<()> {
    let entry = Entry::new(SERVICE, vendor)?;
    if key.trim().is_empty() {
        // Deleting a key that was never stored is not an error — the user's
        // intent ("no key here") is already satisfied.
        let _ = entry.delete_credential();
        return Ok(());
    }
    entry.set_password(key.trim())?;
    Ok(())
}

pub fn load(vendor: &str) -> Option<String> {
    Entry::new(SERVICE, vendor)
        .ok()?
        .get_password()
        .ok()
        .filter(|key| !key.is_empty())
}

/// Which vendors have a key, without revealing any of them.
///
/// The settings panel needs to show "configured" or "empty"; it never needs the
/// value back, and sending it to the webview would put it somewhere the
/// keychain was chosen to avoid.
pub fn configured() -> Vec<String> {
    VENDORS
        .iter()
        .filter(|vendor| load(vendor).is_some())
        .map(|vendor| vendor.to_string())
        .collect()
}

/// Every stored key, as environment variables for the backend process.
pub fn as_env() -> Vec<(String, String)> {
    VENDORS
        .iter()
        .filter_map(|vendor| load(vendor).map(|key| (vendor.to_string(), key)))
        .collect()
}
