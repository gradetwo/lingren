"""Regression coverage for errors observed while diagnosing song production."""
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import jobs, score_converter
from app.adapters.base import BaseAdapter
from app.adapters.stub import StubAdapter
from app.jobs import JobStore
from app.schemas import SynthesizeIn, ScoreIn


def request(models=('stub',)):
    return SynthesizeIn(parts=[dict(
        label=f'part_{i}', voice_id='v', model=model,
        score=dict(bpm=120, notes=[dict(lyric='你', midi=64,
                                      start_s=0, dur_s=.5,
                                      phoneme='zh_ni3')]))
        for i, model in enumerate(models)])


def store_at(tmp_path):
    store = JobStore(tmp_path)
    store.voices['v'] = {'audio_path': 'unused.wav'}
    return store


@pytest.mark.parametrize('error', [ValueError('bad phoneme'),
                                  subprocess.TimeoutExpired('driver', 3600)])
def test_unexpected_error_has_terminal_receipt(tmp_path, monkeypatch, error):
    class Broken(BaseAdapter):
        def synthesize(self, *args):
            raise error

    monkeypatch.setattr(jobs, '_adapters', lambda: {'stub': Broken()})
    store = store_at(tmp_path)
    job = store.create_job(request())
    store._run(job['job_id'])
    receipt = json.loads((store.jobs_dir / job['job_id'] / 'receipt.json').read_text())
    assert receipt['status'] == job['status'] == 'failed'
    assert str(error) in receipt['error']
    assert receipt['timings']['run_s'] >= 0


def test_receipt_is_readable_when_terminal_status_becomes_visible(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    original = store._set
    def inspect(job, **updates):
        if updates.get('status') in ('completed', 'failed'):
            receipt = json.loads((store.jobs_dir / job['job_id'] / 'receipt.json').read_text())
            assert receipt['status'] == updates['status']
        original(job, **updates)
    monkeypatch.setattr(store, '_set', inspect)
    monkeypatch.setattr(store, '_alignment', lambda *args: [])
    job = store.create_job(request())
    store._run(job['job_id'])
    assert job['status'] == 'completed'
    take = json.loads((store.jobs_dir / job['job_id'] / 'receipt.json').read_text())['parts']['part_0']['takes'][0]
    assert take['timings']['synthesis_s'] >= 0
    assert take['timings']['alignment_s'] >= 0


def test_adapter_released_before_switch_and_at_job_end(tmp_path, monkeypatch):
    events = []
    class Tracked(StubAdapter):
        def __init__(self, name): self.name = name
        def synthesize(self, *args):
            events.append(('synth', self.name))
            return super().synthesize(*args)
        def close(self): events.append(('close', self.name))
    adapters = {name: Tracked(name) for name in ('a', 'b')}
    monkeypatch.setattr(jobs, '_adapters', lambda: adapters)
    store = store_at(tmp_path)
    monkeypatch.setattr(store, '_alignment', lambda *args: [])
    job = store.create_job(request(('a', 'a', 'b')))
    store._run(job['job_id'])
    assert events[:4] == [('synth','a'), ('synth','a'), ('close','a'), ('synth','b')]
    assert events[-1] == ('close','b')


def test_get_job_returns_snapshot(tmp_path):
    store = store_at(tmp_path)
    job = store.create_job(request())
    snapshot = store.get_job(job['job_id'])
    snapshot['status'] = 'tampered'
    snapshot['request']['parts'].clear()
    assert store.get_job(job['job_id'])['status'] == 'queued'
    assert store.get_job(job['job_id'])['request']['parts']


def test_mandarin_g2p_is_initialized_once_for_a_score(monkeypatch):
    calls = []
    class FakeG2p:
        def __init__(self): calls.append('init')
        def __call__(self, ch, **kw): return ['ni3' if ch == '你' else 'hao3']
    monkeypatch.setitem(sys.modules, 'g2pM', types.SimpleNamespace(G2pM=FakeG2p))
    cached = getattr(score_converter, '_mandarin_g2p', None)
    if cached: cached.cache_clear()
    try:
        score = ScoreIn(bpm=120, notes=[dict(lyric=ch, midi=64, start_s=i*.5, dur_s=.5)
                                        for i, ch in enumerate('你好你好')])
        result = score_converter.soulx_target_meta(score)
        assert result['phoneme'] == 'zh_ni3 zh_hao3 zh_ni3 zh_hao3'
        assert calls == ['init']
    finally:
        if cached: cached.cache_clear()
