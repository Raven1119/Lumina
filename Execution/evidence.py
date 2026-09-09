"""Immutable, attributed observations from the authorized Execution workspace."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from Nervous.storage import canonical, fingerprint, read_json, write_json


def workspace_path(workspace, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('workspace_relative_path_required')
    path = (workspace / relative).resolve()
    if not path.is_relative_to(workspace) or path == workspace:
        raise ValueError('workspace_path_escape')
    return path


class EvidenceStore:
    """Source content never changes; current file membership is a separate view."""
    def __init__(self, directory, workspace):
        self.directory, self.workspace = Path(directory), Path(workspace).resolve(strict=True)
        self.directory.mkdir(parents=True, exist_ok=True)

    def put(self, text, label, *, kind='observed_text', origin='execution'):
        if not isinstance(text, str) or not isinstance(label, str):
            raise ValueError('invalid_source_text')
        ref = 'source:' + fingerprint([label, text, origin, kind])[:24]
        record = {'ref': ref, 'label': label, 'text': text, 'origin': origin, 'source_kind': kind}
        path = self.directory / (ref.replace(':', '-') + '.json')
        if path.exists():
            if read_json(path) != record:
                raise ValueError('source_identity_conflict')
        else:
            write_json(path, record)
        return record

    def read(self, ref):
        if not isinstance(ref, str) or not re.fullmatch(r'source:[0-9a-f]{24}', ref):
            raise ValueError('unknown_execution_source')
        path = self.directory / (ref.replace(':', '-') + '.json')
        if not path.exists():
            raise ValueError('unknown_execution_source')
        record = read_json(path)
        if record.get('ref') != ref or ref != 'source:' + fingerprint([
                record['label'], record['text'], record['origin'], record['source_kind']])[:24]:
            raise ValueError('source_identity_conflict')
        return record

    def files(self):
        stack, entries, files = [self.workspace], 0, []
        while stack:
            with os.scandir(stack.pop()) as children:
                for child in children:
                    entries += 1
                    if entries > 128:
                        raise ValueError('workspace_entry_bound')
                    if child.is_symlink():
                        raise ValueError('workspace_symlink_not_authorized')
                    if child.name.startswith('.'):
                        continue
                    if child.is_dir(follow_symlinks=False):
                        stack.append(Path(child.path))
                    elif child.is_file(follow_symlinks=False):
                        files.append(Path(child.path))
        return sorted(files)

    def file(self, relative):
        path = workspace_path(self.workspace, relative)
        with path.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            raw = stream.read(64001)
            if len(raw) <= 64000:
                try:
                    text = raw.decode('utf-8-sig')
                except UnicodeDecodeError:
                    pass
                else:
                    record = self.put(text, relative)
                    return {'file': relative, 'ref': record['ref'], 'chars': len(text), 'kind': 'observed_text'}
            stream.seek(0)
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        record = self.put(canonical({'file': relative, 'bytes': size, 'sha256': digest,
            'scope': 'Observed file metadata; not content or business correctness.',
            'body_unavailable': 'not UTF-8 text or exceeds 64000 bytes'}), relative, kind='file_metadata')
        return {'file': relative, 'ref': record['ref'], 'chars': len(record['text']), 'kind': 'file_metadata'}

    def snapshot(self, watched_files=()):
        paths = set(self.files())
        paths.update(path for relative in watched_files
                     if (path := workspace_path(self.workspace, relative)).is_file())
        files = [self.file(path.relative_to(self.workspace).as_posix()) for path in sorted(paths)]
        if sum(item['kind'] == 'observed_text' for item in files) > 12:
            raise ValueError('workspace_evidence_bound_requires_smaller_scope')
        catalogue = self.put(canonical(files), 'current workspace file catalogue', kind='catalogue')
        sources, unread_observation_refs = [], []
        size = 0
        watched = set(watched_files)
        for item in sorted(files, key=lambda item: item['file'] not in watched):
            record = self.read(item['ref'])
            length = len(canonical(record).encode('utf-8'))
            observation = item['file'] in watched
            if ((observation and size + length <= 18000)
                    or (len(record['text']) <= 600 and size + length <= 6000 and len(sources) < 6)):
                sources.append(record)
                size += length
            elif observation and item['kind'] == 'observed_text':
                unread_observation_refs.append(item['ref'])
        return {'files': files, 'sources': sources, 'catalogue_ref': catalogue['ref'],
                'unread_observation_refs': unread_observation_refs}

    def read_result(self, refs, *, analysis=False):
        if not isinstance(refs, (tuple, list)) or not 1 <= len(refs) <= 16:
            raise ValueError('invalid_evidence_refs')
        records = [self.read(ref) for ref in refs]
        text = canonical({'read_result': 'sources-v1', 'sources': [
            {key: record[key] for key in ('ref', 'text', 'origin')} for record in records]})
        text_limit, observation_limit = (24000, 25000) if analysis else (8000, 9000)
        observation = {'capability': 'read_evidence', 'text': text, 'origin': 'execution'}
        metadata = [{key: value for key, value in record.items() if key != 'text'} for record in records]
        content = {'observation': observation, 'records': metadata}
        if (len(text) > text_limit or len(canonical(observation)) > observation_limit
                or len(canonical(content).encode('utf-8')) > 28000):
            observation['text'] = canonical({'read_result': 'capacity-v1', 'status': 'not_read',
                'reason': 'response_capacity', 'required_text_chars': len(text),
                'required_observation_chars': len(canonical(observation)),
                'max_text_chars': text_limit, 'max_observation_chars': observation_limit,
                'max_event_content_bytes': 28000,
                'sources': [{'ref': record['ref'], 'text_chars': len(record['text']), 'origin': record['origin']}
                            for record in records]})
            return {'observation': observation, 'records': []}
        return content
