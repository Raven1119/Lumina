"""Actual read-only history export, without mounting any owner/provider directory."""
import json
import os

import pytest

from Execution.sandbox import DockerIPython
from Nervous.storage import fingerprint, write_json


@pytest.mark.skipif(os.environ.get('LUMINA_TEST_CORE_DOCKER') != '1', reason='explicit isolated history Docker smoke')
def test_actual_history_helper_reads_projection_but_cannot_modify_it(tmp_path):
    workspace, directory = tmp_path / 'workspace', tmp_path / 'history'
    workspace.mkdir(); directory.mkdir()
    ref = 'execution-history:test-run:decision-000001'
    filename = fingerprint(ref) + '.json'
    path = directory / filename
    write_json(path, {'ref': ref, 'content': {'output': 'Observed only A.', 'truncated': True, 'original_output_chars': 12000}})
    before = path.read_bytes(), path.stat().st_mtime_ns
    control = DockerIPython(workspace, history_directory=directory)
    try:
        result = control.execute('import json; print(json.dumps(read_history(' + repr(ref) + ')))')
        assert result.ok
        projection = json.loads(result.output)
        assert json.loads(projection['text'])['truncated'] is True
        blocked = control.execute('from pathlib import Path; Path(' + repr('/lumina-history/' + filename) + ').write_text("changed")')
        assert not blocked.ok and blocked.error_code == 'execution_error'
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
        assert control.execute('print("kernel still usable")').ok
    finally:
        control.close()
