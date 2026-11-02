import multiprocessing
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import soulx_driver
from app.adapters.base import AdapterError
from app.adapters.soulx_session import SoulXSession


def test_worker_reuses_runtime_and_closes_on_end(tmp_path, monkeypatch):
    loaded = []
    class Runtime:
        def __init__(self, options): loaded.append(options)
        def synthesize(self, request): return {'seed': request['seed'], 'voice': request['voice']}
    monkeypatch.setattr(soulx_driver, 'SoulXRuntime', Runtime)
    parent, child = multiprocessing.Pipe()
    thread = threading.Thread(target=soulx_driver.serve,
                              args=(child, {'device': 'fake'}, str(tmp_path/'worker.log')))
    thread.start()
    try:
        for voice, seed in [('liu', 2), ('xiang', 9)]:
            parent.send({'voice': voice, 'seed': seed})
            assert parent.poll(5)
            assert parent.recv() == {'result': {'voice': voice, 'seed': seed}}
        parent.send(None)
        thread.join(5)
        assert not thread.is_alive()
        assert len(loaded) == 1
    finally:
        parent.close()


def test_worker_error_is_returned_and_stops(tmp_path, monkeypatch):
    class Broken:
        def __init__(self, options): raise ValueError('bad model')
    monkeypatch.setattr(soulx_driver, 'SoulXRuntime', Broken)
    parent, child = multiprocessing.Pipe()
    thread = threading.Thread(target=soulx_driver.serve,
                              args=(child, {}, str(tmp_path/'worker.log')))
    thread.start()
    try:
        parent.send({'seed': 1})
        assert parent.poll(5)
        assert parent.recv()['error'] == 'ValueError: bad model'
        thread.join(5)
        assert not thread.is_alive()
    finally:
        parent.close()


def test_timeout_terminates_worker_and_close_is_idempotent():
    events = []
    class Pipe:
        def send(self, value): events.append(('send', value))
        def poll(self, timeout): return False
        def close(self): events.append('pipe_closed')
    class Process:
        alive = True
        def is_alive(self): return self.alive
        def join(self, timeout=None): pass
        def terminate(self): self.alive=False; events.append('terminated')
        def close(self): events.append('process_closed')
    session = SoulXSession.__new__(SoulXSession)
    session.connection, session.process = Pipe(), Process()
    with pytest.raises(AdapterError, match='timed out'):
        session.synthesize({'seed': 0}, timeout=.01)
    session.close()
    assert events.count('terminated') == 1
    assert events.count('pipe_closed') == events.count('process_closed') == 1
