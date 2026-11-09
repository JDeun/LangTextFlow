"""Relocatable POSIX companion; this file did not produce the recorded native run.

Only path bindings, the equivalent public baseline-helper identity and its
recorded-version lookup differ from the preserved private driver. No product
defaults change; reference text is scoring-only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import resource
import secrets
import signal
import subprocess
import sys
import time
import wave
from collections import Counter
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(os.environ['LTF_OWN_HISTORY_EVIDENCE']).resolve()
FIXTURES = Path(os.environ['LTF_ASR_EVIDENCE']).resolve()
BASELINE_DRIVER = Path(os.environ['LTF_ASR_MATCHED_COMPANION']).resolve()
BASELINE_DRIVER_SHA = 'd8157ea2f80a4c08a8c178c1b8e01736c1abe5e29a96ae4ead107facb1714083'
EXPECTED_VERSIONS = json.loads((Path(__file__).parent / '2026-11-09-asr-base-small-matched-cpu.json').read_text())['execution']['versions']
assert hashlib.sha256(BASELINE_DRIVER.read_bytes()).hexdigest() == BASELINE_DRIVER_SHA
spec = importlib.util.spec_from_file_location('frozen_matched_baseline', BASELINE_DRIVER)
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)
sha, now, write_new = baseline.sha, baseline.now, baseline.write_new
ORDER = ('off', 'on', 'on', 'off', 'off', 'on')


def versions():
    found = {name: importlib.metadata.version(name) for name in EXPECTED_VERSIONS if name != 'python'}
    import platform
    found['python'] = platform.python_version()
    assert found == EXPECTED_VERSIONS, 'environment changed from matched CT2 4.8.2 baseline'
    return found


def product():
    result = baseline.product()
    from langtextflow.correction_benchmark import numeric_token_metrics
    from langtextflow.correction_safety import protected_tokens
    return (*result, numeric_token_metrics, protected_tokens)


class History:
    def __init__(self, enabled):
        self.enabled = enabled
        self.scope = None
        self.text = ''
        self.origin = None
        self.reason = 'new_owner'

    def clear(self, reason):
        self.text, self.origin, self.reason = '', None, reason

    def request(self, original, scope, tokenizer):
        if scope != self.scope:
            self.clear('session_language_or_clip_changed')
            self.scope = scope
        hint = self.text[-256:] if self.enabled else ''
        prefix = original.context.title
        assert prefix == 'ASR prototype'
        assert not original.context.description and not original.context.reference_excerpt(1200)
        assert not original.context.presenter and not original.context.asr_hotwords(source_language=scope[1])

        def prompt(value):
            return prefix + (' | ' + value if value else '')

        def tokens(value):
            return len(tokenizer.encode(' ' + value.strip(), add_special_tokens=False).ids)

        while hint and tokens(prompt(hint)) > 128:
            hint = hint[1:]  # Unicode boundaries, never partial UTF-8 bytes.
        payload = original.model_dump(mode='json')
        payload['context']['description'] = hint
        request = type(original).model_validate(payload)
        metadata = {
            'enabled': self.enabled, 'hint_chars': len(hint), 'hint_tokens': tokens(hint) if hint else 0,
            'prompt_tokens': tokens(prompt(hint)),
            'prompt_sha256': hashlib.sha256(prompt(hint).encode()).hexdigest(),
            'hint_sha256': hashlib.sha256(hint.encode()).hexdigest() if hint else None,
            'hint_origin': self.origin if hint else None, 'clear_reason': self.reason,
        }
        assert len(hint) <= 256 and metadata['prompt_tokens'] <= 128
        return request, metadata

    def remember(self, text, scope, chunk_index):
        if not text.strip():
            self.clear('empty_output')
            return
        self.text = text
        self.origin = {'scope_sha256': hashlib.sha256(json.dumps(scope).encode()).hexdigest(),
                       'chunk_index': chunk_index, 'hypothesis_sha256': hashlib.sha256(text.encode()).hexdigest(),
                       'source': 'previous_successful_uncorrected_same_clip_asr_only'}
        self.reason = None


def repeated_ngrams(text):
    words = text.split()
    counts = Counter(tuple(words[i:i+4]) for i in range(max(0, len(words)-3)))
    return sum(count-1 for count in counts.values() if count > 1)


def decode(engine, convert, original, history, scope, index, offset, raw):
    started = time.perf_counter()
    request, prompt = history.request(original, scope, engine._model.hf_tokenizer)
    policy_seconds = time.perf_counter() - started
    previous = history.text
    engine._audio_cursor_samples = offset
    recoveries = engine.timestamp_recovery_count
    row = {'index': index, 'start_sample': offset, 'end_sample': offset + len(raw)//2,
           'prompt': prompt, 'policy_construction_seconds': policy_seconds}
    before = time.perf_counter()
    try:
        segments = engine._transcribe_chunk_sync(convert(raw), request)
        row['decode_convert_seconds'] = time.perf_counter() - before
        text = ' '.join(s.text.strip() for s in segments if s.text.strip())
        assert len(text) <= 20_000, 'bounded hypothesis exceeded'
        row.update(status='ok', text=text, segments=[vars(s) for s in segments],
                   empty_output=not bool(text), timestamp_recoveries=engine.timestamp_recovery_count-recoveries,
                   repeated_fourgram_proxy=repeated_ngrams(text),
                   own_previous_exact_copy_proxy=bool(text and previous and text == previous))
        update = time.perf_counter()
        history.remember(text, scope, index)
        row['history_update_seconds'] = time.perf_counter() - update
    except Exception as error:
        row.update(status='failed', error_category=type(error).__name__,
                   decode_convert_seconds=time.perf_counter()-before)
        history.clear('decode_failure')
        row['history_update_seconds'] = 0.0
    row['processing_seconds'] = sum(row[key] for key in
        ('policy_construction_seconds', 'decode_convert_seconds', 'history_update_seconds'))
    return row


def arm(plan_path, index):
    Engine, convert, quality, request, numeric, protected = product()
    plan = json.loads(plan_path.read_text())
    assert sha(__file__) == plan['driver_sha256'] and versions() == plan['versions']
    assert baseline.verify_models() == plan['models']
    assert sha(FIXTURES / 'fixtures.json') == plan['fixture_manifest_sha256']
    manifest = json.loads((FIXTURES / 'fixtures.json').read_text())
    output = plan_path.parent / f'arm-{index}-{ORDER[index]}'
    output.mkdir()
    engine = Engine(lambda _: None, model=str(baseline.MODELS['base']), device='cpu',
                    compute_type='int8', cpu_threads=2, allow_download=False)
    started, loadavg_start = now(), list(os.getloadavg())
    load = time.perf_counter()
    engine._model = engine._load_model_sync()
    load_seconds = time.perf_counter() - load
    history = History(ORDER[index] == 'on')
    rows, silence = [], []
    for clip in manifest['samples']:
        path = FIXTURES / clip['path']
        assert sha(path) == clip['sha256_wav']
        with wave.open(str(path), 'rb') as wav:
            raw = wav.readframes(wav.getnframes())
        assert hashlib.sha256(raw).hexdigest() == clip['sha256_pcm16']
        scope = (f'pilot-arm-{index}', 'ko', str(clip['id']))
        chunk_rows = []
        for ordinal, (offset, chunk) in enumerate(baseline.chunks(raw, 4)):
            row = decode(engine, convert, request, history, scope, ordinal, offset, chunk)
            chunk_rows.append(row)
            if row['status'] != 'ok':
                break
        text = ' '.join(r['text'] for r in chunk_rows if r['status'] == 'ok' and r['text'])
        failed = any(r['status'] != 'ok' for r in chunk_rows)
        # References enter scoring only after all real input has been decoded.
        score = quality(clip['reference'], text)
        numbers = numeric(clip['reference'], text)
        expected_numbers, negation_count = protected(clip['reference'], 'ko')
        result = {'id': clip['id'], 'policy': ORDER[index], 'reference': clip['reference'],
                  'sha256_pcm16': clip['sha256_pcm16'], 'audio_duration_seconds': clip['duration_seconds'],
                  'status': 'failed' if failed else 'ok', 'hypothesis': text,
                  'empty_hypothesis': not bool(text), 'metrics_even_if_failed': score,
                  'numeric': numbers, 'numeric_insertion_without_reference_digits': bool(not expected_numbers and numbers['actual']),
                  'negation': {'expected_lexical_count': negation_count, 'preservation_score': None,
                               'scope': 'N/A: existing frozen lexical scorer covers zero of these references; excludes prohibition cue'},
                  'repeated_fourgram_proxy': repeated_ngrams(text), 'chunks': chunk_rows}
        result['processing_seconds'] = sum(r['processing_seconds'] for r in chunk_rows)
        result['processing_rtf'] = result['processing_seconds'] / clip['duration_seconds']
        rows.append(result)
        write_new(output / f'clip-{clip["selection_index"]:02d}.json', result)
        # Same history, no special pre-clear: one separate known-zero control per clip.
        tail = decode(engine, convert, request, history, scope, len(chunk_rows),
                      clip['sample_count'], bytes(4 * 16000 * 2))
        tail.update(id=clip['id'], known_zero_pcm=True, control_seconds=4,
                    nonempty_on_known_zero=bool(tail.get('text')), real_clip_metric_member=False)
        silence.append(tail)
        write_new(output / f'silence-{clip["selection_index"]:02d}.json', tail)
        print(json.dumps({'arm': index, 'policy': ORDER[index], 'clip': clip['id'],
                          'status': result['status'], 'cer': score['cer'],
                          'silence_status': tail['status'], 'silence_nonempty': tail['nonempty_on_known_zero']}), flush=True)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {'status': 'completed', 'arm': index, 'policy': ORDER[index], 'model': 'base',
              'pid': os.getpid(), 'started_at': started, 'ended_at': now(),
              'load_seconds': load_seconds, 'loadavg_start': loadavg_start,
              'loadavg_end': list(os.getloadavg()), 'rows': rows, 'silence_controls': silence,
              'process_lifetime_ru_maxrss_raw': peak, 'process_lifetime_peak_rss_mib': peak/1048576,
              'rss_scope': 'Darwin self-child lifetime peak through scoring/clip serialization, before arm serialization; not process tree',
              'source_files': baseline.clean_source(), 'driver_sha256': sha(__file__)}
    write_new(output / 'arm.json', report)


def run():
    baseline.clean_source()
    assert (ROOT / 'selfcheck.json').exists(), 'freeze and pass fake contracts before native run'
    assert json.loads((ROOT / 'selfcheck.json').read_text())['driver_sha256'] == sha(__file__)
    assert (ROOT / 'timer-slot.json').exists(), 'coordinate host timing before native load'
    manifest = json.loads((FIXTURES / 'fixtures.json').read_text())
    root = ROOT / ('run-' + time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '-' + secrets.token_hex(3))
    root.mkdir()
    plan = {'source_commit': baseline.SOURCE, 'source_files': baseline.clean_source(),
            'driver_sha256': sha(__file__), 'baseline_driver_sha256': BASELINE_DRIVER_SHA,
            'fixture_manifest_sha256': sha(FIXTURES / 'fixtures.json'),
            'models': baseline.verify_models(), 'versions': versions(), 'order': list(ORDER),
            'model': 'base', 'window_seconds': 4, 'cpu_threads': 2, 'beam_size': 1,
            'vad_filter': True, 'condition_on_previous_text': False,
            'random_seed': 'unchanged product default', 'planned_real_clip_observations': 48,
            'planned_separate_zero_controls': 48, 'unique_real_clips': 8,
            'numeric_reference_coverage': 3, 'negation_reference_coverage': 0,
            'history_policy': 'only previous successful uncorrected same-clip own hypothesis; <=256 Unicode chars and <=128 total initial-prompt tokens; clear on scope/failure/empty',
            'limitations': ['8 unique KO read clips, not 48 independent clips', 'no semantic negation/word-aligned hallucination qualification',
                            'same host, incidental load/cache warmth uncontrolled', 'decode timing not live/commit latency',
                            'silence controls are synthetic and separately counted', 'no default, six-direction, optimal-model or field promotion']}
    assert len(manifest['samples']) == 8
    write_new(root / 'plan.json', plan)
    deadline, results = time.monotonic() + 20 * 60, []
    try:
        for index, policy in enumerate(ORDER):
            budget = min(120, deadline-time.monotonic())
            if budget <= 0:
                raise TimeoutError('whole_pilot_budget')
            with (root / f'arm-{index}-{policy}.log').open('x') as log:
                child = subprocess.Popen([sys.executable, __file__, 'arm', '--plan', str(root/'plan.json'), '--index', str(index)],
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                    env={**os.environ, 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1',
                         'HF_DATASETS_OFFLINE':'1', 'OMP_NUM_THREADS':'2', 'OPENBLAS_NUM_THREADS':'2', 'VECLIB_MAXIMUM_THREADS':'2'})
                print(json.dumps({'stage':'arm_started','arm':index,'policy':policy,'pid':child.pid,'run':root.name}), flush=True)
                try:
                    code = child.wait(timeout=budget)
                except BaseException:
                    with suppress(ProcessLookupError): os.killpg(child.pid, signal.SIGTERM)
                    try: child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        with suppress(ProcessLookupError): os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=3)
                    results.append({'arm':index,'policy':policy,'pid':child.pid,'returncode':child.returncode,'interrupted':True})
                    raise
            artifact = root/f'arm-{index}-{policy}'/'arm.json'
            results.append({'arm':index,'policy':policy,'pid':child.pid,'returncode':code,
                            'report_sha256':sha(artifact) if artifact.exists() else None})
            print(json.dumps({'stage':'arm_finished',**results[-1]}), flush=True)
    finally:
        write_new(root/'summary.json', {'source_commit':baseline.SOURCE,'finished_at':now(),
            'status':'completed' if len(results)==6 and all(r['returncode']==0 for r in results) else 'incomplete',
            'results':results,'plan_sha256':sha(root/'plan.json')})


def selfcheck():
    Engine, convert, _, request, _, protected = product()
    class Tokenizer:
        def encode(self, text, **kwargs): return SimpleNamespace(ids=list(text))
    class Fake:
        hf_tokenizer = Tokenizer()
        def __init__(self): self.prompts, self.output = [], 'OWN_HYPOTHESIS'
        def transcribe(self, audio, **kwargs):
            self.prompts.append(kwargs)
            assert 'GOLD_FORBIDDEN' not in kwargs['initial_prompt']
            assert kwargs['condition_on_previous_text'] is False and kwargs['vad_filter'] is True
            duration = len(audio)/16000
            return ([SimpleNamespace(text=self.output,start=0.,end=duration)] if self.output else []), SimpleNamespace(duration=duration,duration_after_vad=duration)
    scope, raw = ('s1','ko','clip1'), bytes(16000*2)
    policies = [History(False), History(True)]
    first = []
    for policy in policies:
        engine = Engine(lambda _:None,device='cpu',compute_type='int8',cpu_threads=2)
        engine._model = Fake()
        row = decode(engine,convert,request,policy,scope,0,0,raw)
        first.append(row['prompt']['prompt_sha256'])
        row = decode(engine,convert,request,policy,scope,1,16000,raw)
        assert bool(row['prompt']['hint_origin']) == policy.enabled
        if policy.enabled:
            assert row['prompt']['hint_origin']['hypothesis_sha256'] == hashlib.sha256(b'OWN_HYPOTHESIS').hexdigest()
            assert engine._model.prompts[-1]['initial_prompt'] == 'ASR prototype | OWN_HYPOTHESIS'
        policy.remember('🙂과거'*200,scope,1)
        _, metadata = policy.request(request,scope,Tokenizer())
        assert metadata['hint_chars']<=256 and metadata['prompt_tokens']<=128
        for changed in [('s2','ko','clip1'),('s2','en','clip1'),('s2','en','clip2')]:
            _, metadata = policy.request(request,changed,Tokenizer())
            assert metadata['hint_chars']==0
            policy.remember('OWN_HYPOTHESIS',changed,1)
        policy.scope = scope
        policy.remember('OWN_HYPOTHESIS',scope,1)
        engine._model.output = ''
        tail = decode(engine,convert,request,policy,scope,2,32000,bytes(4*16000*2))
        assert bool(tail['prompt']['hint_origin']) == policy.enabled and not policy.text
        policy.remember('OWN_HYPOTHESIS',scope,1)
        def fail(*_): raise RuntimeError('synthetic failure')
        engine._transcribe_chunk_sync = fail
        assert decode(engine,convert,request,policy,scope,3,0,raw)['status']=='failed' and not policy.text
    assert first[0]==first[1]
    polluted = request.model_dump(mode='json')
    polluted['context']['reference_text'] = 'GOLD_FORBIDDEN'
    try:
        History(True).request(type(request).model_validate(polluted), scope, Tokenizer())
    except AssertionError:
        pass
    else:
        raise AssertionError('controlled pilot must refuse reference context')
    manifest=json.loads((FIXTURES/'fixtures.json').read_text())
    assert sum(bool(protected(c['reference'],'ko')[0]) for c in manifest['samples'])==3
    assert sum(bool(protected(c['reference'],'ko')[1]) for c in manifest['samples'])==0
    return {'status':'passed','classification':'fake_contract_only_no_native_models',
            'checks':['same first prompt','prior OWN output only','Unicode/token bounds','scope/failure/empty clear',
                      'zero-tail no preclear','gold sentinel absent','numeric3/negation0 coverage'],
            'driver_sha256':sha(__file__),'baseline_driver_sha256':BASELINE_DRIVER_SHA,'source_files':baseline.clean_source()}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('selfcheck','run','arm'))
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--index',type=int)
    args=parser.parse_args()
    if args.mode=='selfcheck':
        value=selfcheck();write_new(ROOT/'selfcheck.json',value);print(json.dumps(value),flush=True)
    elif args.mode=='arm':
        try:
            arm(args.plan,args.index)
        except BaseException as error:
            write_new(args.plan.parent/f'arm-{args.index}-failure.json',
                      {'status':'failed','arm':args.index,'error_category':type(error).__name__,'at':now()})
            raise
    else:
        run()
