"""Standard-library installation and package tests; writes only temporary directories."""
from pathlib import Path
import importlib.util
import json
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lui_install', ROOT / 'install.py')
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / '项目 With Spaces'
        self.project.mkdir()
    def tearDown(self):
        self.temp.cleanup()
    def run_install(self, agent='codex', replace=False):
        return mod.install(ROOT, project=self.project, agent=agent, replace=replace)
    def test_project_install(self):
        result = self.run_install()
        self.assertEqual(result['status'], 'installed')
        dest = Path(result['skill'])
        self.assertTrue((dest / 'assets/tokens.css').is_file())
        self.assertTrue((dest / 'assets/motion-reference.html').is_file())
        self.assertIn('lumina-interface-language', (self.project / 'AGENTS.md').read_text())
    def test_idempotent(self):
        self.run_install(); before = (self.project / 'AGENTS.md').read_bytes()
        self.assertEqual(self.run_install()['status'], 'already current')
        self.assertEqual(before, (self.project / 'AGENTS.md').read_bytes())
        self.assertEqual(before.count(mod.BEGIN.encode()), 1)
    def test_preserve_bom_crlf_and_other_skill(self):
        old = b'\xef\xbb\xbf# Existing\r\n\r\n<!-- BEGIN research-interface-language -->\r\nKeep original.\r\n<!-- END research-interface-language -->\r\n'
        (self.project / 'AGENTS.md').write_bytes(old)
        result = self.run_install()
        self.assertTrue((self.project / 'AGENTS.md').read_bytes().startswith(old))
        self.assertEqual(Path(result['backups'][0]).read_bytes(), old)
    def test_codex_override(self):
        (self.project / 'AGENTS.override.md').write_text('# Effective\n')
        result = self.run_install()
        self.assertEqual(Path(result['instructions']).name, 'AGENTS.override.md')
        self.assertFalse((self.project / 'AGENTS.md').exists())
    def test_empty_override_uses_agents(self):
        (self.project / 'AGENTS.override.md').write_text('  \n')
        self.assertEqual(Path(self.run_install()['instructions']).name, 'AGENTS.md')
    def test_kimi(self):
        result = self.run_install('kimi')
        self.assertIn('.kimi', result['skill'])
        self.assertEqual(Path(result['instructions']).name, 'AGENTS.md')
    def test_claude(self):
        result = self.run_install('claude')
        self.assertIn('.claude', result['skill'])
        self.assertEqual(Path(result['instructions']).name, 'CLAUDE.md')
    def test_modified_refused_and_replace_backed_up(self):
        first = self.run_install(); target = Path(first['skill']) / 'SKILL.md'
        target.write_text('Local changes')
        with self.assertRaises(ValueError): self.run_install()
        result = self.run_install(replace=True)
        self.assertEqual((Path(result['backups'][0]) / 'SKILL.md').read_text(), 'Local changes')
        self.assertEqual(target.read_bytes(), (ROOT / 'SKILL.md').read_bytes())
    def test_malformed_markers_fail_before_writes(self):
        (self.project / 'AGENTS.md').write_text(mod.BEGIN)
        with self.assertRaises(ValueError): self.run_install()
        self.assertFalse((self.project / '.agents').exists())
    def test_symlink_refused(self):
        target = Path(self.temp.name) / 'other'; target.mkdir()
        try: (self.project / '.agents').symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError): self.skipTest('Symlinks unavailable')
        with self.assertRaises(ValueError): self.run_install()
        self.assertEqual(list(target.iterdir()), [])
    def test_global_does_not_write_instructions(self):
        with patch.object(Path, 'home', return_value=self.project):
            result = mod.install(ROOT, project=None, agent='codex', replace=False)
        self.assertIsNone(result['instructions'])
        self.assertFalse((self.project / 'AGENTS.md').exists())
    def test_missing_project_refused(self):
        with self.assertRaises(ValueError):
            mod.install(ROOT, project=self.project/'missing', agent='codex', replace=False)
    def test_manifest_hash_failure(self):
        source = Path(self.temp.name)/'bad'; source.mkdir()
        (source/'bad.txt').write_text('x')
        (source/'manifest.json').write_text(json.dumps({'name':mod.NAME,'files':{'bad.txt':'0'*64}}))
        with self.assertRaises(ValueError): mod.load_manifest(source)
    def test_manifest_traversal_refused(self):
        source = Path(self.temp.name)/'bad'; source.mkdir()
        (source/'manifest.json').write_text(json.dumps({'name':mod.NAME,'files':{'../outside':'0'*64}}))
        with self.assertRaises(ValueError): mod.load_manifest(source)
    def test_atomic_failure_restores_previous_skill(self):
        first = self.run_install(); target = Path(first['skill'])/'SKILL.md'
        target.write_text('Old skill remains')
        (self.project/'AGENTS.md').write_text('Existing unrelated text')
        with patch.object(mod, 'atomic_write', side_effect=OSError('injected failure')):
            with self.assertRaises(OSError): self.run_install(replace=True)
        self.assertEqual(target.read_text(), 'Old skill remains')
        self.assertEqual((self.project/'AGENTS.md').read_text(), 'Existing unrelated text')

if __name__ == '__main__': unittest.main()
