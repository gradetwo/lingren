"""Service full chain for the muscriptor large test (runs on Colab).

Assumes: service code at /content/service (run from there), weights
at /content/muscriptor-large/model.safetensors (+ config.json beside
it), scale.wav at /content/scale.wav. Env for the server:
  SVS_DATA_DIR=/content/svs-data
  MUSCRIPTOR_MODEL=/content/muscriptor-large/model.safetensors
  MUSCRIPTOR_DEVICE=cuda
(no SVS_API_KEY, so the local service auth passes open.)

Steps: start uvicorn detached, wait for /health, POST /v1/transcribe
(no model field, so the env path is used), poll the job, fetch
/midi and /receipt, print everything as JSON lines.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
SERVICE_DIR = "/content/service"
WEIGHTS = "/content/muscriptor-large/model.safetensors"
AUDIO = "/content/scale.wav"


def call(method: str, path: str, body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body,
                                 method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, resp.read()


def main() -> int:
    env = dict(os.environ)
    env["SVS_DATA_DIR"] = "/content/svs-data"
    env["MUSCRIPTOR_MODEL"] = WEIGHTS
    env["MUSCRIPTOR_DEVICE"] = "cuda"
    log = open("/content/service.log", "wb")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000"],
        cwd=SERVICE_DIR, env=env, stdout=log,
        stderr=subprocess.STDOUT, start_new_session=True)
    print(f"server pid={server.pid}", flush=True)
    ok = False
    for _ in range(60):
        try:
            status, body = call("GET", "/health")
            print("health:", status, body.decode()[:200], flush=True)
            ok = True
            break
        except Exception as e:
            time.sleep(2)
    if not ok:
        print("server did not come up", flush=True)
        return 2
    boundary = "----chainboundary"
    with open(AUDIO, "rb") as f:
        audio = f.read()
    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; '
        b'filename="scale.wav"\r\n'
        b"Content-Type: audio/wav\r\n\r\n",
        audio,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    status, body = call("POST", "/v1/transcribe", body=b"".join(parts),
                        headers={"Content-Type":
                                 f"multipart/form-data; boundary={boundary}"})
    submit = json.loads(body.decode())
    print("submit:", json.dumps(submit), flush=True)
    job_id = submit["job_id"]
    job = None
    for _ in range(90):
        time.sleep(10)
        status, body = call("GET", f"/v1/jobs/{job_id}")
        job = json.loads(body.decode())
        print("poll:", json.dumps(job), flush=True)
        if job.get("status") in ("completed", "failed"):
            break
    if not job or job.get("status") != "completed":
        print("chain did not complete", flush=True)
        return 3
    status, midi = call("GET", f"/v1/jobs/{job_id}/midi")
    print("midi:", status, "bytes", len(midi),
          "magic", midi[:4], flush=True)
    status, receipt = call("GET", f"/v1/jobs/{job_id}/receipt")
    print("receipt:", receipt.decode()[:4000], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
