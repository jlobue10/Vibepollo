#!/usr/bin/env python3
"""Text gate for the per-app output log open: by handle, so a reparse point, a device or a
non-disk object cannot be substituted for the log and a pipe server never gets impersonation
rights; a refused handle is closed exactly once and the launch continues. --baseline reads HEAD.
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


process = read('src/process.cpp')
open_log = block(process, 'const auto open_log = [&]()')
failures = 0
for ok, name in (
    ('_wfsopen(' not in process, 'the output log is no longer opened by name through the CRT'),
    (all(t in open_log for t in ('CreateFileW(', 'FILE_FLAG_OPEN_REPARSE_POINT', 'SECURITY_SQOS_PRESENT', 'SECURITY_ANONYMOUS')),
     'the log is opened by handle without following reparse points and without granting impersonation'),
    ('GetFileType(h) != FILE_TYPE_DISK' in open_log and 'FILE_ATTRIBUTE_REPARSE_POINT' in open_log and 'FILE_ATTRIBUTE_DIRECTORY' in open_log,
     'only a plain local disk file is accepted'),
    (open_log.count('CloseHandle(h)') == 2 and '_open_osfhandle(' in open_log and '_wfdopen(' in open_log and '_close(fd)' in open_log,
     'a refused or unbridged handle is closed and an accepted one is owned by the FILE*'),
    ('FILE_APPEND_DATA,' in open_log and 'OPEN_ALWAYS,' in open_log, 'the open keeps append semantics'),
    ('#include <io.h>' in process and '#include <share.h>' not in process, 'the CRT bridge headers replace share.h'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
