"""Bounded lifetime/timeout for a reusable SoulX child; no torch in the API."""
from __future__ import annotations
import multiprocessing
from pathlib import Path

from ..soulx_driver import serve
from .base import AdapterError


class SoulXSession:
    def __init__(self, options: dict, log_path: Path):
        context = multiprocessing.get_context('spawn')
        self.connection, child = context.Pipe()
        self.process = context.Process(target=serve, args=(child, options, str(log_path)), daemon=True)
        try:
            self.process.start()
        except BaseException:
            self.connection.close()
            raise
        finally:
            child.close()

    def synthesize(self, request: dict, timeout: float = 3600) -> dict:
        try:
            self.connection.send(request)
            if not self.connection.poll(timeout):
                raise AdapterError(f'SoulX worker timed out after {timeout}s')
            response = self.connection.recv()
            if 'error' in response:
                raise AdapterError('SoulX worker failed: ' + response['error'])
            return response['result']
        except (EOFError, BrokenPipeError, OSError) as e:
            self.close()
            raise AdapterError(f'SoulX worker exited without a result: {e}') from e
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        self.process = None
        try:
            if process.is_alive():
                try:
                    self.connection.send(None)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                process.join(2)
            if process.is_alive():
                process.terminate()
                process.join(2)
            if process.is_alive():
                process.kill()
            process.join()
        finally:
            self.connection.close()
            process.close()
