//! Private immutable provider options, separate from public model provenance.
use crate::source_context::SourceContext;
use crate::WorkerRequest;
use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TranslationIdentity {
    pub provider: String,
    pub requested_model: Option<String>,
    pub configured_model: Option<String>,
    pub verified_model: Option<String>,
    pub model_revision: Option<String>,
}

impl TranslationIdentity {
    pub(crate) fn native() -> Self {
        Self {
            provider: "m2m100-ct2".into(),
            requested_model: None,
            configured_model: None,
            verified_model: None,
            model_revision: None,
        }
    }
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TranslationOptions {
    pub provider: String,
    pub url: String,
    pub model: String,
    pub api_key: String,
    pub timeout_seconds: f64,
    pub requested_model: Option<String>,
    pub configured_model: Option<String>,
}

fn public_model(model: &str) -> bool {
    let parts = model.split('/').collect::<Vec<_>>();
    !model.is_empty()
        && model.len() <= 200
        && model.as_bytes()[0].is_ascii_alphanumeric()
        && !model.contains("..")
        && !model.contains(":/")
        && !(model.as_bytes().get(1) == Some(&b':') && model.as_bytes()[0].is_ascii_alphabetic())
        && parts.len() <= 2
        && parts.iter().all(|part| part.as_bytes().first().is_some_and(u8::is_ascii_alphanumeric))
        && !["sk-", "hf_", "ghp_", "github_pat_", "xoxb-", "xoxp-"]
            .iter()
            .any(|prefix| model.starts_with(prefix))
        && model
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_.:-/".contains(&c))
}

impl TranslationOptions {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        if !matches!(
            self.provider.as_str(),
            "m2m100-ct2" | "demo" | "ollama" | "openai-compatible"
        ) || self.url.len() > 4096
            || self.api_key.len() > 8192
            || self.model.is_empty()
            || self.model.chars().count() > 200
            || !self.timeout_seconds.is_finite()
            || !(0.1..=600.).contains(&self.timeout_seconds)
            || [&self.requested_model, &self.configured_model]
                .iter()
                .any(|value| value.as_ref().is_some_and(|model| !public_model(model)))
        {
            return Err("invalid_translation_options");
        }
        Ok(())
    }

    pub(crate) fn identity(&self) -> TranslationIdentity {
        TranslationIdentity {
            provider: self.provider.clone(),
            requested_model: self.requested_model.clone(),
            configured_model: self.configured_model.clone(),
            verified_model: None,
            model_revision: None,
        }
    }
}

#[derive(Clone)]
pub struct TranslationProvider {
    pub options: TranslationOptions,
    pub context: SourceContext,
}

impl TranslationProvider {
    pub fn new(options: TranslationOptions, context: SourceContext) -> Result<Self, &'static str> {
        options.validate()?;
        context.validate()?;
        Ok(Self { options, context })
    }

    pub(crate) fn native(&self) -> bool {
        self.options.provider == "m2m100-ct2"
    }

    pub(crate) fn request(&self, base: &WorkerRequest, mode: &str, text: &str) -> WorkerRequest {
        let mut request = base.clone();
        if self.native() {
            let mut payload = serde_json::json!({"text":text,"context":self.context.translation()});
            if mode == "prepare" {
                payload["mode"] = "prepare".into();
            }
            request.payload = payload.to_string();
        } else {
            request.payload = serde_json::json!({"mode":mode,"text":text,
                "context":{"title":self.context.title,"presenter":self.context.presenter,
                    "preset":self.context.preset,"glossary":self.context.glossary,
                    "reference_text":self.context.reference_text},
                "options":{"provider":self.options.provider,"url":self.options.url,
                    "model":self.options.model,"api_key":self.options.api_key,
                    "timeout_seconds":self.options.timeout_seconds}})
            .to_string();
        }
        request
    }
}
