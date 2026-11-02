"""Driver mapping unit tests (no torch, no weights).

events_to_notes is a pure function over duck-typed events; these
tests pin the start/end pairing, the unclosed-start drop rule and
the sort order. The real muscriptor event classes are never
imported here (the driver defers its muscriptor import to main()).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.muscriptor_driver import events_to_notes  # noqa: E402


def _start(index, pitch, start, instrument):
    # events_to_notes dispatches on the class NAME, so the fake
    # events carry the real class names.
    return NoteStartEvent(index, pitch, start, instrument)


def _end(index, end):
    return NoteEndEvent(index, end)


class NoteStartEvent:
    def __init__(self, index, pitch, start_time, instrument):
        self.index = index
        self.pitch = pitch
        self.start_time = start_time
        self.instrument = instrument


class NoteEndEvent:
    def __init__(self, start_event_index, end_time):
        self.start_event_index = start_event_index
        self.end_time = end_time


def test_pairs_by_index_and_sorts():
    events = [
        _start(1, 60, 0.5, "acoustic_piano"),
        _start(0, 69, 0.0, "voice"),
        _end(0, 1.0),
        _end(1, 1.5),
    ]
    notes = events_to_notes(events)
    assert notes == [
        {"instrument": "voice", "pitch": 69, "start_s": 0.0,
         "end_s": 1.0},
        {"instrument": "acoustic_piano", "pitch": 60, "start_s": 0.5,
         "end_s": 1.5},
    ]


def test_unclosed_start_dropped_not_invented():
    events = [_start(0, 69, 0.0, "voice")]
    assert events_to_notes(events) == []


def test_end_without_start_ignored():
    events = [_end(7, 1.0)]
    assert events_to_notes(events) == []
