"""Offline runner contracts; no model packages, weights, speech or network required."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from scripts import benchmark_ming_tts_local as runner


class _Waveform:
    ndim = 2

    def __init__(self, samples):
        self.samples = samples
        self.shape = (1, len(samples))

    def detach(self):
        return self

    def cpu(self):
        return self

    def float(self):
        return self

    def reshape(self, _size):
        return self

    def tolist(self):
        return self.samples


class MingTtsLocalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def options(self, *extra):
        return runner.parse_args([
            "--source-dir", str(self.root / "source"),
            "--model-path", str(self.root / "model"),
            "--output-dir", str(self.root / "output"),
            *extra,
        ])

    def test_default_never_validates_assets_or_imports_models(self):
        with patch.object(runner, "validate_assets", side_effect=AssertionError("assets loaded")), \
                patch.object(runner, "install_native_network_guard",
                             side_effect=AssertionError("kernel guard on default path")), \
                patch.object(runner, "load_dependencies",
                             side_effect=AssertionError("heavy import")):
            report, code = runner.run(self.options())
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "not_exercised")
        self.assertFalse(report["actual_model_execution"])
        self.assertFalse(report["inference_attempted"])
        self.assertFalse(report["field_ready"])
        self.assertEqual(report["completed_case_count"], 0)
        self.assertEqual(list((self.root / "output").glob("*.wav")), [])
        self.assertIsNone(report["first_audio_latency_seconds"])

    def test_import_and_default_cli_work_with_stdlib_only(self):
        # A fresh process makes this independent of other tests' imported packages.
        program = """
import sys
class BlockModels:
    def find_spec(self, fullname, path=None, target=None):
        blocked = {'torch', 'torchaudio', 'transformers', 'ming_tts', 'onnxruntime'}
        if fullname.split('.')[0] in blocked:
            raise AssertionError('unexpected heavy import: ' + fullname)
sys.meta_path.insert(0, BlockModels())
from scripts.benchmark_ming_tts_local import main
raise SystemExit(main(sys.argv[1:]))
"""
        completed = subprocess.run(
            [sys.executable, "-c", program,
             "--source-dir", str(self.root / "missing-source"),
             "--model-path", str(self.root / "missing-model"),
             "--output-dir", str(self.root / "stdlib-output")],
            cwd=Path(runner.__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "not_exercised")

    def test_opt_in_with_missing_directory_is_failed_not_passed(self):
        with patch.object(runner, "load_dependencies", side_effect=AssertionError("heavy import")):
            report, code = runner.run(self.options("--allow-model-execution"))
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["model_load_attempted"])
        self.assertFalse(report["actual_model_execution"])
        self.assertIn("missing", report["error"]["message"])
        saved = json.loads((self.root / "output" / "report.json").read_text())
        self.assertEqual(saved["status"], "failed")

    def test_remote_or_repository_model_identifier_is_rejected(self):
        for value in ("https://example.invalid/model", "hf://owner/model", "owner/model",
                      "//remote/share", "\\\\remote\\share"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "local directory"):
                runner.local_directory(value, "model-path")

    def test_missing_assets_fail_before_hash_or_model_load(self):
        source, model = self.root / "source", self.root / "model"
        source.mkdir()
        model.mkdir()
        with patch.object(runner, "sha256_file", side_effect=AssertionError("heavy hash")), \
                self.assertRaisesRegex(ValueError, "missing local assets"):
            runner.validate_assets(str(source), str(model))

    def test_pinned_checkpoint_size_is_required_before_loading(self):
        source, model = self.root / "source", self.root / "model"
        source.mkdir()
        model.mkdir()
        for root, names in ((source, runner.REQUIRED_SOURCE_FILES),
                            (model, runner.REQUIRED_MODEL_FILES)):
            for name in names:
                (root / name).write_bytes(b"not a model")
        with self.assertRaisesRegex(ValueError, "size differs"):
            runner.validate_assets(str(source), str(model))

    def test_previous_report_is_never_overwritten(self):
        output = self.root / "output"
        output.mkdir()
        report = output / "report.json"
        report.write_text("prior evidence")
        with self.assertRaises(FileExistsError):
            runner.run(self.options())
        self.assertEqual(report.read_text(), "prior evidence")

    def test_bad_pinned_weight_hash_is_rejected(self):
        source, model = self.root / "source", self.root / "model"
        source.mkdir()
        model.mkdir()
        for root, names in ((source, runner.REQUIRED_SOURCE_FILES),
                            (model, runner.REQUIRED_MODEL_FILES)):
            for name in names:
                (root / name).write_bytes(b"fixture")
        # Sparse metadata fixture; no 3 GB model data is allocated or read.
        with (model / "model.safetensors").open("wb") as stream:
            stream.truncate(2959055504)
        git_responses = [SimpleNamespace(stdout=runner.SOURCE_REVISION), SimpleNamespace(stdout="")]
        with patch.object(runner.subprocess, "run", side_effect=git_responses), \
                patch.object(runner, "sha256_file", return_value="0" * 64), \
                self.assertRaisesRegex(ValueError, "model.safetensors SHA256 differs"):
            runner.validate_assets(str(source), str(model))

    def test_out_of_bounds_decode_or_autocast_device_is_not_exercised(self):
        for extra in (("--max-decode-steps", "257"),
                      ("--device", "cuda", "--cpu-autocast")):
            with self.subTest(extra=extra):
                options = self.options(*extra)
                options.output_dir = str(self.root / str(len(extra)))
                report, code = runner.run(options)
                self.assertEqual(code, 1)
                self.assertEqual(report["status"], "failed")
                self.assertFalse(report["actual_model_execution"])

    def test_python_network_attempts_are_denied_and_counted(self):
        guard = runner.PythonSocketGuard()
        with guard:
            with self.assertRaises(runner.OutgoingNetworkDenied):
                socket.getaddrinfo("example.invalid", 443)
            with socket.socket() as client, self.assertRaises(runner.OutgoingNetworkDenied):
                client.connect(("127.0.0.1", 9))
        evidence = guard.report()
        self.assertEqual(evidence["denied_attempt_count"], 2)
        self.assertFalse(evidence["os_packet_audit_performed"])
        self.assertFalse(evidence["native_or_subprocess_egress_audited"])
        self.assertFalse(guard.enabled)

    def test_offline_environment_overrides_then_restores(self):
        with patch.dict(os.environ, {"HF_HUB_OFFLINE": "0"}):
            with runner.offline_environment(4):
                self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
                self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")
                self.assertEqual(os.environ["ORT_DISABLE_TELEMETRY"], "1")
                self.assertEqual(os.environ["OMP_NUM_THREADS"], "4")
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "0")

    def test_all_fixtures_use_zero_speaker_without_reference_audio(self):
        for language in runner.FIXTURES:
            kwargs = runner.fixture_kwargs(language, "US_Emma")
            self.assertTrue(kwargs["use_zero_spk_emb"])
            self.assertFalse(kwargs["use_spk_emb"])
            self.assertNotIn("prompt_wav_path", kwargs)
            self.assertNotIn("spk_emb_path", kwargs)
            self.assertEqual(kwargs["language_id"], language)
            self.assertTrue(kwargs["raw_text"])
        self.assertEqual(runner.fixture_kwargs("en", "UK_George")["controls"],
                         {"ip": "UK_George"})
        self.assertEqual(runner.fixture_kwargs("ja", "US_Emma")["task"], "MULTILINGUAL")

    def test_invalid_waveforms_cannot_be_mistaken_for_usable_output(self):
        for samples in ([], [0.0, 0.0], [float("nan")], [float("inf")]):
            with self.subTest(samples=samples):
                stats = runner.audio_statistics(samples, 24000)
                self.assertFalse(stats["finite"] and stats["nonzero"])
        stats = runner.audio_statistics([0.25, -0.5, 1.5], 24000)
        self.assertTrue(stats["finite"] and stats["nonzero"])
        self.assertEqual(stats["samples_outside_pcm_unit_range"], 1)

    def test_silent_model_result_retains_failure_without_wav_or_quality_pass(self):
        torch = SimpleNamespace(
            set_num_threads=lambda _: None, set_num_interop_threads=lambda _: None,
            manual_seed=lambda _: None,
        )
        model = SimpleNamespace(sample_rate=24000, synthesize=lambda **_: _Waveform([0.0, 0.0]))
        assets = {"source_dir": str(self.root), "model_path": str(self.root)}
        with patch.object(runner, "validate_assets", return_value=assets), \
                patch.object(runner, "install_native_network_guard",
                             return_value={"installed": True, "mechanism": "mock-for-unit-test"}), \
                patch.object(runner, "load_dependencies",
                             return_value=(torch, lambda *_a, **_k: model)):
            report, code = runner.run(self.options("--allow-model-execution", "--case", "en"))
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["inference_attempted"])
        self.assertEqual(report["completed_case_count"], 0)
        self.assertEqual(report["failed_case_count"], 1)
        self.assertFalse(report["field_ready"])
        self.assertEqual(report["semantic_quality"], "not_evaluated")
        self.assertEqual(list((self.root / "output").glob("*.wav")), [])

    def test_native_guard_failure_prevents_dependency_import(self):
        assets = {"source_dir": str(self.root), "model_path": str(self.root)}
        with patch.object(runner, "validate_assets", return_value=assets), \
                patch.object(runner, "install_native_network_guard",
                             side_effect=RuntimeError("kernel isolation unavailable")), \
                patch.object(runner, "load_dependencies") as dependencies:
            report, code = runner.run(self.options("--allow-model-execution"))
        dependencies.assert_not_called()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["phase"], "native_network_isolation")
        self.assertFalse(report["model_load_attempted"])
        self.assertFalse(report["native_network_isolation"]["installed"])

    def test_non_linux_fails_before_loading_guard_or_models(self):
        with patch.object(runner.platform, "system", return_value="Darwin"), \
                patch.object(runner.importlib.util, "spec_from_file_location") as loader, \
                self.assertRaisesRegex(RuntimeError, "requires Linux"):
            runner.install_native_network_guard()
        loader.assert_not_called()

    def test_telemetry_is_disabled_before_ming_import(self):
        onnx = ModuleType("onnxruntime")
        events = []
        onnx.disable_telemetry_events = lambda: events.append("disable_telemetry")
        ming = ModuleType("ming_tts")
        ming.__file__ = str(self.root / "ming_tts.py")
        ming.MingTTS = object()
        torch = ModuleType("torch")
        original_import = __import__

        def observe_import(name, *args, **kwargs):
            if name in {"onnxruntime", "ming_tts", "torch"}:
                events.append(name)
            return original_import(name, *args, **kwargs)

        with patch.dict(sys.modules, {"onnxruntime": onnx, "ming_tts": ming, "torch": torch}), \
                patch("builtins.__import__", side_effect=observe_import):
            result = runner.load_dependencies(self.root)
        self.assertEqual(result, (torch, ming.MingTTS))
        self.assertLess(events.index("disable_telemetry"), events.index("ming_tts"))
        self.assertLess(events.index("disable_telemetry"), events.index("torch"))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux-only kernel isolation")
    def test_kernel_guard_installs_only_in_isolated_subprocess(self):
        program = """
import json
from scripts.benchmark_ming_tts_local import install_native_network_guard
print(json.dumps(install_native_network_guard()))
"""
        completed = subprocess.run(
            [sys.executable, "-c", program], cwd=Path(runner.__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        evidence = json.loads(completed.stdout)
        self.assertTrue(evidence["installed"])
        self.assertTrue(evidence["thread_synchronized"])
        self.assertEqual(len(evidence["native_socket_probes"]), 4)
        self.assertTrue(all(row["blocked"] for row in evidence["native_socket_probes"]))
        # The child filter must not affect this long-lived test process.
        with socket.socket() as parent_socket:
            self.assertGreaterEqual(parent_socket.fileno(), 0)

    def test_wav_encoding_and_hashes_are_reproducible(self):
        a = runner.write_wav(self.root / "a.wav", [0.0, 0.25, -0.25, 2.0], 24000)
        b = runner.write_wav(self.root / "b.wav", [0.0, 0.25, -0.25, 2.0], 24000)
        self.assertEqual(a["wav_sha256"], b["wav_sha256"])
        self.assertEqual(a["pcm16_sha256"], b["pcm16_sha256"])
        with self.assertRaises(FileExistsError):
            runner.write_wav(self.root / "a.wav", [0.1], 24000)


if __name__ == "__main__":
    unittest.main()
