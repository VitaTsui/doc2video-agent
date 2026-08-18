//! Which model to use, remembered between launches.
//!
//! Not a secret, so unlike the API keys this lives in a plain file rather than
//! the keychain. It has to live on *this* side at all because the backend
//! freezes its settings for the life of the process (`get_settings` is an
//! `lru_cache`): the choice can only take effect by being handed to a new one,
//! so something outside that process has to remember it.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Clone, Default)]
#[serde(default)]
pub struct Prefs {
    /// mock | auto | agent_cli | anthropic | openai | gemini | compatible.
    /// Empty means the backend's own default, which is to hold no model.
    pub provider: String,
    /// Empty means the provider's own default; the compatible channel has none.
    pub model: String,
    /// Required by the compatible channel, optional elsewhere.
    pub base_url: String,
}

fn path(app_data: &Path) -> PathBuf {
    app_data.join("model.json")
}

pub fn load(app_data: &Path) -> Prefs {
    std::fs::read_to_string(path(app_data))
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

pub fn save(app_data: &Path, prefs: &Prefs) -> Result<(), String> {
    let text = serde_json::to_string_pretty(prefs).map_err(|e| e.to_string())?;
    std::fs::write(path(app_data), text).map_err(|e| format!("无法保存设置：{e}"))
}

impl Prefs {
    /// As environment variables for the backend process.
    ///
    /// Empty fields are left out rather than passed as empty strings: the
    /// backend distinguishes "not set" (use my default) from "set to nothing",
    /// and the compatible channel refuses to start without a base_url.
    pub fn as_env(&self) -> Vec<(String, String)> {
        [
            ("D2V_LLM_PROVIDER", &self.provider),
            ("D2V_LLM_MODEL", &self.model),
            ("D2V_LLM_BASE_URL", &self.base_url),
        ]
        .into_iter()
        .filter(|(_, value)| !value.trim().is_empty())
        .map(|(name, value)| (name.to_string(), value.trim().to_string()))
        .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_unset_choice_passes_nothing_so_the_backend_keeps_its_default() {
        assert!(Prefs::default().as_env().is_empty());
    }

    #[test]
    fn only_the_filled_fields_travel() {
        let prefs = Prefs {
            provider: "anthropic".into(),
            model: "  ".into(),
            base_url: String::new(),
        };
        assert_eq!(
            prefs.as_env(),
            vec![("D2V_LLM_PROVIDER".to_string(), "anthropic".to_string())]
        );
    }

    #[test]
    fn a_missing_file_is_not_an_error() {
        let prefs = load(Path::new("/nonexistent/doc2video"));
        assert!(prefs.provider.is_empty());
    }
}
