"""Run the muscriptor driver with per-line stdout timestamps.

The driver itself does not split model-load time from transcribe time.
It prints ProgressEvent lines only after the model has loaded, so:
  load_time ~= t(first progress line) - t(process start)
  transcribe_time ~= t(summary line) - t(first progress line)
RTF is reported both ways: transcribe-only and wall-total, each
divided into the input audio duration.

Usage:
  python3 run-timed.py -- <driver argv...>
Output: the driver's stdout lines, each prefixed with [t+SS.ss],
followed by a TIMING JSON object on the last line.
"""

import json
import subprocess
import sys
import time


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] == "--":
        argv = argv[1:] if argv else []
    if not argv:
        print("usage: run-timed.py -- <driver argv...>", file=sys.stderr)
        return 2
    t0 = time.monotonic()
    first_progress = None
    summary_at = None
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        now = time.monotonic() - t0
        print(f"[t+{now:7.2f}] {line}", end="")
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") == "progress" and first_progress is None:
            first_progress = now
        if obj.get("type") == "summary":
            summary_at = now
    _, stderr = proc.communicate()
    total = time.monotonic() - t0
    if stderr:
        print("--- driver stderr tail ---", file=sys.stderr)
        print(stderr[-2000:], file=sys.stderr)
    timing = {
        "exit_code": proc.returncode,
        "wall_total_s": round(total, 3),
        "load_s": (round(first_progress, 3)
                   if first_progress is not None else None),
        "transcribe_s": (round(summary_at - first_progress, 3)
                         if first_progress is not None
                         and summary_at is not None else None),
    }
    print("TIMING " + json.dumps(timing))
    return proc.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
