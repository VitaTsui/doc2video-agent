//! The providers this person has set up, and which one is answering.
//!
//! Not secrets, so unlike the API keys this lives in a plain file rather than
//! the keychain. It has to live on *this* side at all because the backend
//! freezes its settings for the life of the process (`get_settings` is an
//! `lru_cache`): a choice can only take effect by being handed to a new one,
//! so something outside that process has to remember it.
//!
//! **A list, not a choice from a fixed menu.** Model ids and vendors move
//! faster than a shipped desktop app does, and the previous shape — pick one
//! of five names we compiled in — meant DeepSeek, Kimi or a company's own
//! gateway could only be reached by us shipping a release naming them. What
//! cannot be opened up is the protocol: Anthropic, OpenAI and Gemini are three
//! different request formats in three different SDKs, and a local CLI is not
//! an HTTP API at all. So the protocol is chosen from the four that exist in
//! the code, and everything else — the name, the address, the model id — is
//! typed in.
//!
//! The backend still receives exactly one provider, resolved here at spawn.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// One configured way to reach a model.
#[derive(Serialize, Deserialize, Clone, Default)]
#[serde(default)]
pub struct Provider {
    /// Stable for the life of the entry; also its account in the keychain, so
    /// two OpenAI-compatible gateways can hold two different keys.
    pub id: String,
    /// Whatever the person calls it. Shown everywhere; means nothing to us.
    pub name: String,
    /// anthropic | openai | gemini | compatible | agent_cli — the four request
    /// shapes the pipeline actually implements, plus the local CLIs.
    pub protocol: String,
    pub base_url: String,
    /// Passed to the provider verbatim. We never validate it: a model id we
    /// have not heard of is far more likely to be new than wrong.
    pub model: String,
}

#[derive(Serialize, Deserialize, Clone, Default)]
#[serde(default)]
pub struct Prefs {
    pub providers: Vec<Provider>,
    /// Which provider's id answers. Empty means no model at all, which is a
    /// supported way to run: the script is then written by hand or by MCP.
    pub active: String,

    // -- what a pre-list install wrote ------------------------------------
    // Read once, on the next launch, and turned into a single entry. Kept so
    // that upgrading does not silently drop the model someone had configured.
    pub provider: String,
    pub model: String,
    pub base_url: String,
}

fn path(app_data: &Path) -> PathBuf {
    app_data.join("model.json")
}

pub fn load(app_data: &Path) -> Prefs {
    let mut prefs: Prefs = std::fs::read_to_string(path(app_data))
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default();
    prefs.migrate();
    prefs
}

pub fn save(app_data: &Path, prefs: &Prefs) -> Result<(), String> {
    let text = serde_json::to_string_pretty(prefs).map_err(|e| e.to_string())?;
    std::fs::write(path(app_data), text).map_err(|e| format!("无法保存设置：{e}"))
}

/// The environment variable each protocol's key is read from by the backend.
///
/// Empty for the local CLIs, which need no key — that is the whole point of
/// them, and the settings list says so rather than showing an empty box.
pub fn key_var(protocol: &str) -> &'static str {
    match protocol {
        "anthropic" => "ANTHROPIC_API_KEY",
        "openai" => "OPENAI_API_KEY",
        "gemini" => "GEMINI_API_KEY",
        "compatible" => "D2V_COMPATIBLE_API_KEY",
        _ => "",
    }
}

impl Prefs {
    /// Fold a pre-list configuration into the list, once.
    fn migrate(&mut self) {
        if !self.providers.is_empty() || self.provider.trim().is_empty() {
            return;
        }
        let protocol = self.provider.trim().to_string();
        self.providers.push(Provider {
            id: format!("p_{protocol}"),
            name: protocol.clone(),
            protocol,
            base_url: std::mem::take(&mut self.base_url),
            model: std::mem::take(&mut self.model),
        });
        self.active = self.providers[0].id.clone();
        self.provider.clear();
    }

    pub fn chosen(&self) -> Option<&Provider> {
        self.providers.iter().find(|p| p.id == self.active)
    }

    /// As environment variables for the backend process.
    ///
    /// Empty fields are left out rather than passed as empty strings: the
    /// backend distinguishes "not set" (use my default) from "set to nothing",
    /// and the compatible channel refuses to start without a base_url.
    pub fn as_env(&self) -> Vec<(String, String)> {
        let Some(provider) = self.chosen() else {
            return Vec::new();
        };
        [
            ("D2V_LLM_PROVIDER", &provider.protocol),
            ("D2V_LLM_MODEL", &provider.model),
            ("D2V_LLM_BASE_URL", &provider.base_url),
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

    fn provider(id: &str, protocol: &str) -> Provider {
        Provider {
            id: id.into(),
            name: id.into(),
            protocol: protocol.into(),
            base_url: String::new(),
            model: "some-model".into(),
        }
    }

    #[test]
    fn an_unset_choice_passes_nothing_so_the_backend_keeps_its_default() {
        assert!(Prefs::default().as_env().is_empty());
    }

    #[test]
    fn only_the_chosen_provider_travels() {
        // Several configured, one active: the backend takes one at a time and
        // handing it two would be handing it an ambiguity.
        let prefs = Prefs {
            providers: vec![provider("a", "anthropic"), provider("b", "openai")],
            active: "b".into(),
            ..Default::default()
        };
        let env = prefs.as_env();
        assert!(env.contains(&("D2V_LLM_PROVIDER".to_string(), "openai".to_string())));
        assert!(!env.iter().any(|(_, value)| value == "anthropic"));
    }

    #[test]
    fn only_the_filled_fields_travel() {
        let prefs = Prefs {
            providers: vec![Provider {
                id: "a".into(),
                name: "a".into(),
                protocol: "anthropic".into(),
                base_url: String::new(),
                model: "  ".into(),
            }],
            active: "a".into(),
            ..Default::default()
        };
        assert_eq!(
            prefs.as_env(),
            vec![("D2V_LLM_PROVIDER".to_string(), "anthropic".to_string())]
        );
    }

    #[test]
    fn an_active_id_naming_nothing_is_no_model_rather_than_a_wrong_one() {
        let prefs = Prefs {
            providers: vec![provider("a", "anthropic")],
            active: "deleted".into(),
            ..Default::default()
        };
        assert!(prefs.as_env().is_empty());
    }

    #[test]
    fn a_configuration_from_before_the_list_becomes_an_entry_in_it() {
        // Otherwise upgrading silently drops the model someone had set up, and
        // the first thing they would notice is the agent saying it has none.
        let mut prefs = Prefs {
            provider: "compatible".into(),
            model: "deepseek-chat".into(),
            base_url: "https://api.deepseek.com/v1".into(),
            ..Default::default()
        };
        prefs.migrate();

        assert_eq!(prefs.providers.len(), 1);
        assert_eq!(prefs.chosen().unwrap().protocol, "compatible");
        assert_eq!(prefs.chosen().unwrap().model, "deepseek-chat");
        assert_eq!(prefs.chosen().unwrap().base_url, "https://api.deepseek.com/v1");

        // And only once — a second pass must not add a duplicate.
        prefs.migrate();
        assert_eq!(prefs.providers.len(), 1);
    }

    #[test]
    fn each_protocol_names_the_variable_its_key_is_read_from() {
        assert_eq!(key_var("anthropic"), "ANTHROPIC_API_KEY");
        assert_eq!(key_var("compatible"), "D2V_COMPATIBLE_API_KEY");
        // The local CLIs take no key, which is why they are worth having.
        assert_eq!(key_var("agent_cli"), "");
    }

    #[test]
    fn a_missing_file_is_not_an_error() {
        let prefs = load(Path::new("/nonexistent/doc2video"));
        assert!(prefs.providers.is_empty());
    }
}
