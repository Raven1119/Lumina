#!/usr/bin/env python3
"""Offline package integrity, palette and frozen motion verification (standard library)."""
from pathlib import Path
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parent

def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()

def script(text, name):
    m = re.search(r'<script id="' + re.escape(name) + r'">(.*?)</script>', text, re.S)
    if not m:
        raise ValueError("Missing script " + name)
    return m.group(1)

def function_slice(text, name):
    if name == 'targetCursor':
        return text[text.index(' function targetCursor('):text.index(' function hideCursor(')]
    if name == 'hideCursor':
        return text[text.index(' function hideCursor('):text.index(" document.addEventListener('pointermove'")]
    start = text.index(' function stepTo(')
    end = text.index('\n }\n', start) + len('\n }\n')
    return text[start:end]

def verify(root=ROOT):
    import importlib.util
    spec = importlib.util.spec_from_file_location('lui_install_verify', root / 'install.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    manifest = installer.load_manifest(root)
    if (root / 'VERSION').read_text().strip() != manifest['version']:
        raise ValueError('Version mismatch')
    tokens = json.loads((root / 'assets/tokens.json').read_text())
    if set(tokens['themes']) != {'mineral', 'depth', 'stone'}:
        raise ValueError('Unexpected palettes')
    for key, value in tokens['themes']['stone']['values'].items():
        if re.fullmatch(r'#[a-fA-F0-9]{6}', value):
            rgb = [int(value[i:i+2], 16) for i in [1, 3, 5]]
            if len(set(rgb)) != 1:
                raise ValueError('Non-neutral Stone token: ' + key)
        elif value.startswith('rgba('):
            rgb = value[5:-1].split(',')[:3]
            if len(set(rgb)) != 1:
                raise ValueError('Non-neutral Stone alpha token: ' + key)
    token_css = (root / 'assets/tokens.css').read_text()
    for theme in tokens['themes'].values():
        for key, value in theme['values'].items():
            if f'--lui-{key}:{value};' not in token_css:
                raise ValueError('Token CSS drift: ' + key)
    lock = json.loads((root / 'assets/motion-source-lock.json').read_text())
    gallery = (root / 'assets/motion-reference.html').read_text()
    for name in ['reference.html', 'motion-reference.html']:
        data = (root / 'assets' / name).read_text()
        for identifier, expected in lock['script_locks'].items():
            if sha(script(data, identifier)) != expected:
                raise ValueError('Motion drift: ' + name + '/' + identifier)
        app = script(data, 'lumina-application')
        for fun, expected in lock['function_locks'].items():
            if fun == 'stepTo' and name == 'reference.html':
                continue
            if sha(function_slice(app, fun)) != expected:
                raise ValueError('Function drift: ' + name + '/' + fun)
        if re.search(r'data-(?:theme|palette)="dusk"|@keyframes (?:respond|mini|idle-)', data):
            raise ValueError('Obsolete custom motion/purple theme present')
    motion_map = json.loads((root / 'assets/motion-map.json').read_text())
    ids = [item['id'] for item in motion_map['items']]
    if len(ids) != 15 or len(set(ids)) != 15:
        raise ValueError('Expected 15 unique motion entries')
    for item in motion_map['items']:
        if item['reference']['anchor'] not in script(gallery, item['reference']['script_id']):
            raise ValueError('Broken source locator: ' + item['id'])
    forbidden = {'.woff', '.woff2', '.ttf', '.otf', '.ttc'}
    if any(p.suffix.lower() in forbidden for p in root.rglob('*')):
        raise ValueError('Font binaries are not part of this package')
    # Check all static local resources in the HTML, without opening external links.
    for p in root.rglob('*.html'):
        text = p.read_text()
        for url in re.findall(r'(?:src|href)="([^"]+)"', text):
            if url.startswith(('http:', 'https:', 'data:', '#', 'mailto:')) or '${' in url:
                continue
            relative = url.split('?')[0].split('#')[0]
            if relative and not (p.parent / relative).resolve().is_file():
                raise ValueError(f'Broken link in {p.name}: {url}')
    return {'files': len(manifest['files']), 'themes': 3, 'motion_entries': 15, 'frozen_blocks': 'PASS', 'integrity': 'PASS'}

if __name__ == '__main__':
    try:
        print(json.dumps(verify(), ensure_ascii=False, indent=2))
    except Exception as exc:
        print('FAIL: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
