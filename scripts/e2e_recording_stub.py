"""Owned browser fixture: real app/PCM/WAV, synthetic ASR, no model loading."""
from __future__ import annotations

import uvicorn
from test_recording_pcm_clock import SampleClockAsr

from langtextflow.main import app, runtime

runtime._faster_whisper_engine = lambda **kwargs: SampleClockAsr()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8002, log_level="warning")
