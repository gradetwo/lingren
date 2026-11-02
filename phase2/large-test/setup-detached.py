"""Detached Colab setup for the muscriptor large test.

Spawns `pip install muscriptor pydantic scipy` detached (log at
/content/setup.log) and returns immediately, so the caller can poll
the log instead of blocking on the install. Prints GPU + torch
state first (Colab runtimes usually ship torch already).
"""

import subprocess
import sys

print("torch probe:")
probe = subprocess.run(
    [sys.executable, "-c",
     "import torch; print('torch', torch.__version__, "
     "'cuda', torch.cuda.is_available())"],
    capture_output=True, text=True)
print(probe.stdout.strip() or probe.stderr.strip()[-500:])
log = open("/content/setup.log", "wb")
proc = subprocess.Popen(
    [sys.executable, "-m", "pip", "install", "-q",
     "muscriptor", "pydantic", "scipy"],
    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
print(f"DETACHED pip pid={proc.pid} log=/content/setup.log")
