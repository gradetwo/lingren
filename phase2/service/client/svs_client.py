"""Yeban-side reference client for the 伶人 service (Phase 1).

Stdlib-only (urllib) so it can be vendored into any caller. It mirrors
the two MCP tools in design section 6: synthesize_singing (submit) and
svs_job (poll + fetch). Yeban's own MCP tool wrappers are a thin layer
over this module; the module itself never touches Yeban code.

Voice-cloning note: reference audio must be the caller's own recording
or audio they are licensed to clone. The service never trains on it and
never redistributes it.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path


class SvsClientError(Exception):
    pass


class LingrenClient:
    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _req(self, method: str, path: str, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if headers:
            h.update(headers)
        req = urllib.request.Request(self.base_url + path, data=data,
                                     headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            raise SvsClientError(f"{method} {path} -> {e.code}: "
                                 f"{e.read()[:500]!r}") from e

    def _get_bytes(self, path: str) -> bytes:
        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + path, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raise SvsClientError(f"GET {path} -> {e.code}") from e

    def upload_voice(self, audio_path: str | Path,
                     prompt_meta_path: str | Path | None = None,
                     name: str | None = None) -> dict:
        """Multipart upload (stdlib multipart encoding)."""
        boundary = "----lingren-voice"
        parts = []
        audio = Path(audio_path).read_bytes()
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="file"; filename="{Path(audio_path).name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode()
            + audio + b"\r\n")
        if prompt_meta_path is not None:
            meta = Path(prompt_meta_path).read_bytes()
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; "
                'name="prompt_meta"; filename="prompt_meta.json"\r\n'
                "Content-Type: application/json\r\n\r\n".encode()
                + meta + b"\r\n")
        if name:
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="name"\r\n\r\n{name}\r\n'.encode())
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        h = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + "/v1/voices",
                                     data=body, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise SvsClientError(f"POST /v1/voices -> {e.code}: "
                                 f"{e.read()[:500]!r}") from e

    def synthesize(self, parts: list[dict]) -> dict:
        """Submit; returns the job receipt stub (job_id + totals)."""
        return self._req("POST", "/v1/synthesize", {"parts": parts})

    def job_status(self, job_id: str) -> dict:
        return self._req("GET", f"/v1/jobs/{job_id}")

    def wait(self, job_id: str, timeout_s: float = 3600.0,
             poll_s: float = 5.0) -> dict:
        """Poll until completed/failed; returns the final status dict."""
        deadline = time.time() + timeout_s
        while True:
            st = self.job_status(job_id)
            if st["status"] in ("completed", "failed"):
                return st
            if time.time() > deadline:
                raise SvsClientError(f"job {job_id} timed out")
            time.sleep(poll_s)

    def fetch_receipt(self, job_id: str) -> dict:
        return self._req("GET", f"/v1/jobs/{job_id}/receipt")

    def fetch_audio(self, job_id: str, part: str, take: int) -> bytes:
        return self._get_bytes(
            f"/v1/jobs/{job_id}/audio?part={part}&take={take}")
