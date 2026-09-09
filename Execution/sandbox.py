"""Execution's bounded IPython process in an isolated task workspace.

Docker restrictions reuse the Tycho-derived pattern retained in
Mind/docs/cognition_minimal/ISOLATED_MODEL_SOURCE_AUDIT.md and TYCHO_LICENSE.txt.
Only the explicitly authorized business workspace is mounted; no host fallback.
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import uuid
from pathlib import Path

from Execution.ipython_control import IPythonResult
from Nervous.storage import canonical

# Installed interpreter image; the tag identifies an artifact, not a runtime contract.
IMAGE_TAG = 'lumina-execution-ipython:d2'

_KERNEL = r'''
import contextlib, json, os, sys, tempfile
from IPython.core.interactiveshell import InteractiveShell
sys.path.insert(0, '/workspace')
shell=InteractiveShell.instance(user_ns={})
requests=[]
def request_mind(question, evidence_files=(), model_ref=''):
    if requests: raise ValueError('Only one Mind request per completed cell.')
    if not isinstance(question,str) or not question.strip() or len(question)>1000:
        raise ValueError('A Mind question must contain 1..1000 characters.')
    if not isinstance(evidence_files,(list,tuple)) or len(evidence_files)>3:
        raise ValueError('Supply at most three evidence files.')
    if any(not isinstance(f,str) or not f or len(f)>128 or f.startswith(('/', '\\')) or ':' in f
           or '..' in f.replace('\\','/').split('/') for f in evidence_files):
        raise ValueError('Evidence files must be bounded relative paths.')
    if not isinstance(model_ref,str) or len(model_ref)>128: raise ValueError('Invalid model ref.')
    text=json.dumps(dict(question=question,evidence_files=list(evidence_files),model_ref=model_ref),ensure_ascii=False)
    if len(text)>2000: raise ValueError('Mind request exceeds 2000 characters.')
    requests.append(text)
    return {'status':'queued_until_cell_commits'}
shell.user_ns['request_mind']=request_mind
for line in sys.stdin:
    request=json.loads(line)
    requests.clear()
    saved_out, saved_err = os.dup(1), os.dup(2)
    with tempfile.TemporaryFile(mode='w+',encoding='utf-8',errors='replace') as output:
        try:
            os.dup2(output.fileno(), 1); os.dup2(output.fileno(), 2)
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                result=shell.run_cell(request['code'], store_history=False)
                output.flush()
        finally:
            os.dup2(saved_out, 1); os.dup2(saved_err, 2)
            os.close(saved_out); os.close(saved_err)
        output.seek(0); text=output.read(10000); char_count=len(text)
        while True:
            chunk=output.read(65536)
            if not chunk: break
            char_count += len(chunk)
    error=result.error_before_exec or result.error_in_exec
    response={'request_id':request['request_id'],'ok':error is None,'output':text[:10000],
              'error_code':'execution_error' if error is not None else None,
              'error':(type(error).__name__+': '+str(error))[:500] if error is not None else None,
              'truncated':char_count>10000,'original_output_chars':char_count}
    if requests and error is None: response['cognitive_request']=requests[0]
    encoded=json.dumps(response,ensure_ascii=False)+'\n'
    if len(encoded.encode('utf-8'))>65536: raise ValueError('Correlated reply exceeds transport capacity.')
    sys.stdout.write(encoded); sys.stdout.flush()
'''


class DockerIPython:
    """Same small control interface as PersistentIPython; no host fallback.

    Restriction flags reuse Lumina's existing Tycho-derived Docker pattern;
    provenance/license remain in the retained isolated-computation source audit.
    """
    def __init__(self, workspace, *, image=IMAGE_TAG, timeout=20):
        self.workspace = Path(workspace).resolve(strict=True)
        self.image, self.timeout = image, timeout
        self.name = 'lumina-execution-' + uuid.uuid4().hex
        self.process = None
        self.replies = queue.Queue(maxsize=1)
        self.closed = False
        self.failure_diagnostic = None  # Trusted host only; never part of IPythonResult.
        self._stderr_tail, self._stderr_bytes = b'', 0
        self._stderr_done = threading.Event()

    def command(self):
        return ['docker', 'run', '--rm', '--pull', 'never', '--name', self.name,
                '--init', '-i', '--network', 'none', '--log-driver', 'none',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--pids-limit', '64', '--memory', '256m', '--memory-swap', '256m',
                '--cpus', '1', '--ulimit', 'nofile=128:128', '--ulimit', 'fsize=8388608:8388608',
                '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777',
                '--user', '65534:65534', '--workdir', '/workspace', '--env', 'HOME=/tmp',
                '--mount', f'type=bind,source={self.workspace},target=/workspace',
                self.image, 'python', '-I', '-B', '-u', '-c', _KERNEL]

    def _start(self):
        self.process = subprocess.Popen(self.command(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def receive():
            while True:
                line = self.process.stdout.readline(65537)
                if not line or len(line) > 65536:
                    value = {'transport_error': 'container_output_unavailable_or_oversized'}
                else:
                    try:
                        value = json.loads(line)
                    except ValueError:
                        value = {'transport_error': 'container_output_protocol'}
                try:
                    self.replies.put_nowait(value)
                except queue.Full:
                    return
                if 'transport_error' in value:
                    return
        def drain():
            try:
                while chunk := self.process.stderr.read(8192):
                    self._stderr_bytes += len(chunk)
                    self._stderr_tail = (self._stderr_tail + chunk)[-4096:]
            except (OSError, ValueError):
                pass  # The owner may close a stream while stopping the container.
            finally:
                self._stderr_done.set()
        threading.Thread(target=receive, daemon=True).start()
        threading.Thread(target=drain, daemon=True).start()

    def execute(self, code):
        if self.closed:
            return IPythonResult(False, error_code='kernel_closed')
        if not isinstance(code, str) or not code or len(code) > 20000:
            return IPythonResult(False, error_code='invalid_or_oversized_code')
        phase, reason = 'start', 'transport_exception'
        try:
            if self.process is None:
                self._start()
            request_id = uuid.uuid4().hex
            phase = 'request_write'
            self.process.stdin.write((canonical({'request_id': request_id, 'code': code}) + '\n').encode())
            self.process.stdin.flush()
            phase = 'reply_wait'
            value = self.replies.get(timeout=self.timeout)
            if 'transport_error' in value:
                reason = value['transport_error'] if value['transport_error'] in {
                    'container_output_unavailable_or_oversized', 'container_output_protocol'} else 'container_output_protocol'
                raise RuntimeError(reason)
            phase = 'reply_decode'
            if value.pop('request_id', None) != request_id:
                reason = 'isolated_reply_identity_mismatch'
                raise RuntimeError(reason)
            return IPythonResult(**value)
        except (OSError, ValueError, TypeError, RuntimeError, queue.Empty) as error:
            self.failure_diagnostic = {'phase': phase,
                'reason': 'reply_timeout' if isinstance(error, queue.Empty) else reason,
                'exception_type': type(error).__name__,
                'exit_code_before_cleanup': self.process.poll() if self.process is not None else None}
            try:
                self.close()
            finally:
                self.failure_diagnostic.update(
                    exit_code_after_cleanup=self.process.poll() if self.process is not None else None,
                    stderr_tail=self._stderr_tail.decode('utf-8', errors='replace'),
                    stderr_bytes=self._stderr_bytes, stderr_truncated=self._stderr_bytes > len(self._stderr_tail))
            return IPythonResult(False, error_code='isolated_kernel_failed', error=type(error).__name__)

    def interrupt(self):
        self.close()
        return True

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process is not None:
            subprocess.run(['docker', 'rm', '-f', self.name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10, check=False)
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
            self._stderr_done.wait(timeout=.2)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
