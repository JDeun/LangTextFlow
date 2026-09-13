from __future__ import annotations

import math

from hypothesis import given, settings, strategies as st

from langtextflow.telemetry import decode_pcm_f32le


@settings(max_examples=100, deadline=None)
@given(st.binary(max_size=4096))
def test_pcm_decoder_never_returns_non_finite_samples(payload: bytes) -> None:
    try:
        samples = decode_pcm_f32le(payload, max_frame_bytes=4096)
    except ValueError:
        return
    assert all(math.isfinite(value) for value in samples)
    assert all(abs(value) <= 4.0 for value in samples)


@settings(max_examples=100, deadline=None)
@given(st.integers(min_value=1, max_value=2048))
def test_pcm_decoder_rejects_non_float32_alignment(size: int) -> None:
    if size % 4 == 0:
        return
    try:
        decode_pcm_f32le(bytes(size), max_frame_bytes=4096)
    except ValueError:
        return
    raise AssertionError("misaligned float32 payload was accepted")
