//! Bounded immutable application settings; model preparation remains a leaf.
use serde::{Deserialize, Serialize};

pub const MAX_AUDIO_FRAME_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_WHISPER_SAMPLES: u64 = 120 * 16_000;

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioOptions {
    pub protocol_version: u16,
    pub chunk_samples: u64,
    pub vad_threshold_dbfs: f64,
    pub vad_hangover_frames: usize,
    pub max_frame_bytes: usize,
    pub max_segments: usize,
}

impl Default for AudioOptions {
    fn default() -> Self {
        Self {
            protocol_version: 1,
            chunk_samples: 4 * 16_000,
            vad_threshold_dbfs: -45.,
            vad_hangover_frames: 3,
            max_frame_bytes: 1024 * 1024,
            max_segments: 100,
        }
    }
}

impl AudioOptions {
    pub fn valid(&self) -> bool {
        self.protocol_version == 1
            && (1..=MAX_WHISPER_SAMPLES).contains(&self.chunk_samples)
            && self.vad_threshold_dbfs.is_finite()
            && self.vad_hangover_frames <= 10_000
            && (4..=MAX_AUDIO_FRAME_BYTES).contains(&self.max_frame_bytes)
            && self.max_frame_bytes.is_multiple_of(4)
            && (1..=10_000).contains(&self.max_segments)
    }

    pub(crate) fn window_bytes(&self) -> usize {
        self.chunk_samples as usize * 4
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WhisperOptions {
    pub protocol_version: u16,
    pub device: String,
    pub compute_type: String,
}

impl WhisperOptions {
    pub fn valid(&self) -> bool {
        self.protocol_version == 1
            && matches!(self.device.as_str(), "cpu" | "cuda" | "auto")
            && matches!(
                self.compute_type.as_str(),
                "default"
                    | "auto"
                    | "int8"
                    | "int8_float32"
                    | "int8_float16"
                    | "int8_bfloat16"
                    | "int16"
                    | "float16"
                    | "float32"
                    | "bfloat16"
            )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn settings_are_bounded_and_legacy_defaults_remain_unchanged() {
        let mut options = AudioOptions::default();
        assert!(options.valid());
        assert_eq!(
            options.window_bytes(),
            crate::pcm_ingress::WINDOW_SAMPLES * 4
        );
        for chunk in [0, MAX_WHISPER_SAMPLES + 1] {
            options.chunk_samples = chunk;
            assert!(!options.valid());
        }
        options = AudioOptions::default();
        options.max_frame_bytes = MAX_AUDIO_FRAME_BYTES;
        options.max_segments = 10_000;
        options.vad_hangover_frames = 10_000;
        assert!(options.valid());
        options.max_frame_bytes -= 1;
        assert!(!options.valid());
        options = AudioOptions::default();
        options.vad_threshold_dbfs = f64::NAN;
        assert!(!options.valid());
        let mut whisper = WhisperOptions {
            protocol_version: 1,
            device: "cuda".into(),
            compute_type: "float16".into(),
        };
        assert!(whisper.valid()); // Syntax only; no native/GPU readiness claim.
        whisper.device = "private/model".into();
        assert!(!whisper.valid());
    }
}
