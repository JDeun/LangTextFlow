//! Immutable inference inputs; Python retains the existing glossary/ASR semantics.
use crate::mt_slice::{ContextBytes, TranslationContext};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceContext {
    pub title: String,
    pub presenter: Option<String>,
    pub description: String,
    pub reference_text: String,
    pub preset: String,
    pub hotwords: Vec<String>,
    pub glossary: Vec<Value>,
}

impl Default for SourceContext {
    fn default() -> Self {
        Self {
            title: "ASR prototype".into(),
            presenter: None,
            description: String::new(),
            reference_text: String::new(),
            preset: "general".into(),
            hotwords: vec![],
            glossary: vec![],
        }
    }
}

impl SourceContext {
    pub(crate) fn translation(&self) -> TranslationContext {
        TranslationContext {
            preset: self.preset.clone(),
            glossary: self.glossary.clone(),
        }
    }

    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        if self.title.trim().is_empty()
            || self.title.chars().count() > 120
            || self
                .presenter
                .as_ref()
                .is_some_and(|text| text.chars().count() > 120)
            || self.description.chars().count() > 1000
            || self.reference_text.chars().count() > 3000
            || self.hotwords.len() > 256
            || self
                .hotwords
                .iter()
                .any(|text| text.trim().is_empty() || text.chars().count() > 160)
        {
            return Err("invalid_source_context");
        }
        // Count without allocating a serialized copy of caller-provided values.
        let mut bytes = ContextBytes(0);
        serde_json::to_writer(&mut bytes, self).map_err(|_| "invalid_source_context")?;
        if bytes.0 > 48 * 1024 {
            return Err("source_context_limit");
        }
        self.translation().validate()
    }
}
