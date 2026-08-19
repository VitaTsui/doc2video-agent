//! Model API keys, in the OS credential store.
//!
//! Keys never reach a config file. They are read out of the keychain at launch
//! and handed to the backend as environment variables, which is also why
//! changing one restarts the backend rather than being applied live: the
//! backend caches its settings for the life of the process (`get_settings` is
//! an `lru_cache`), so a restart is the only honest way to apply a change.
//!
//! Stored per provider entry rather than per vendor, because two entries can
//! share a protocol — a DeepSeek gateway and a Kimi one are both
//! OpenAI-compatible and hold different keys. The account is the entry's id;
//! which environment variable it becomes is decided at spawn, from the
//! protocol (see `prefs::key_var`).

use anyhow::Result;
use keyring::Entry;

const SERVICE: &str = "com.vitahsu.doc2video";

/// What a pre-list install stored its keys under: one per vendor.
///
/// Still read, never written. An upgrade turns the old single choice into one
/// provider entry, and that entry's key is whatever was already in the
/// keychain under its vendor name — moving it would be a migration that can
/// fail, and reading through is one line.
pub const LEGACY_VENDORS: [&str; 4] = [
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

/// One provider entry's key, falling back to what its protocol used to store.
///
/// The fallback is what makes an upgrade invisible: the migrated entry has no
/// key of its own yet, and the one already in the keychain is the same key.
pub fn load_for(id: &str, protocol: &str) -> Option<String> {
    load(id).or_else(|| {
        let vendor = crate::prefs::key_var(protocol);
        if vendor.is_empty() {
            None
        } else {
            load(vendor)
        }
    })
}

/// Which provider entries hold a key, without revealing any of them.
///
/// The settings panel needs to show "configured" or "empty"; it never needs the
/// value back, and sending it to the webview would put it somewhere the
/// keychain was chosen to avoid.
pub fn configured(prefs: &crate::prefs::Prefs) -> Vec<String> {
    prefs
        .providers
        .iter()
        .filter(|provider| load_for(&provider.id, &provider.protocol).is_some())
        .map(|provider| provider.id.clone())
        .collect()
}

/// The chosen provider's key, as the environment variable it is read from.
///
/// Only the chosen one. Handing the backend every key it might ever need would
/// put three unrelated secrets in the environment of a process that is about
/// to use one of them.
pub fn as_env(prefs: &crate::prefs::Prefs) -> Vec<(String, String)> {
    let Some(provider) = prefs.chosen() else {
        return Vec::new();
    };
    let vendor = crate::prefs::key_var(&provider.protocol);
    if vendor.is_empty() {
        return Vec::new();
    }
    load_for(&provider.id, &provider.protocol)
        .map(|key| vec![(vendor.to_string(), key)])
        .unwrap_or_default()
}
