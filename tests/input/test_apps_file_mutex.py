#!/usr/bin/env python3
"""Text gate: every web handler that reads and rewrites apps.json holds apps_file_mutex() for the
whole read-modify-write (reorderApps did not, so a concurrent save or catalogue sync was lost).
--baseline reads HEAD.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
    return (ROOT / path).read_text(encoding='utf-8')


def block(source, marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


confighttp = read('src/confighttp.cpp')
failures = 0
for handler in ('void reorderApps(', 'void saveApp(', 'void deleteApp('):
    if handler not in confighttp:
        print('SKIP: ' + handler + ' not present')
        continue
    body = block(confighttp, handler)
    writes = body.count('write_file(config::stream.file_apps')
    ok = writes == 0 or ('apps_file_mutex()' in body and body.index('apps_file_mutex()') < body.index('read_file(config::stream.file_apps'))
    print(('PASS: ' if ok else 'FAIL: ') + handler + ' holds apps_file_mutex() across its read-modify-write of apps.json')
    failures += not ok
sys.exit(1 if failures else 0)
