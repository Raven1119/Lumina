"""Durability failures preserve whole owner records and remain visible."""
from types import SimpleNamespace

import pytest

from Nervous import storage


@pytest.mark.parametrize('cut', ['file_fsync', 'replace', 'directory_fsync'])
def test_atomic_replace_failure_never_leaves_a_partial_owner_record(tmp_path, monkeypatch, cut):
    path = tmp_path / 'state.json'
    storage.write_json(path, {'revision': 1})
    def fail(*args):
        raise OSError('injected persistence failure')
    if cut == 'directory_fsync':
        monkeypatch.setattr(storage, 'sync_directory', fail)
    else:
        monkeypatch.setattr(storage.os, 'fsync' if cut == 'file_fsync' else 'replace', fail)
    with pytest.raises(OSError, match='injected persistence failure'):
        storage.write_json(path, {'revision': 2})
    assert storage.read_json(path) == {'revision': 2 if cut == 'directory_fsync' else 1}
    assert list(tmp_path.glob('.state-*')) == []


def test_posix_directory_fsync_closes_handle_even_on_failure(monkeypatch):
    effects = []
    def fsync(handle):
        effects.append(('fsync', handle))
        raise OSError('directory fsync failed')
    monkeypatch.setattr(storage, 'os', SimpleNamespace(name='posix', O_RDONLY=0, O_DIRECTORY=1,
        open=lambda *args: 42, fsync=fsync, close=lambda handle: effects.append(('close', handle))))
    with pytest.raises(OSError, match='directory fsync failed'):
        storage.sync_directory('owned-directory')
    assert effects == [('fsync', 42), ('close', 42)]
