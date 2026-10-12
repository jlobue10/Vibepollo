#!/usr/bin/env python3
"""Text gate for the Playnite IPC boundary: a parse failure mid-message never returns a partial
catalogue snapshot with its type set (autosync treated it as authoritative), and the named pipe
is created as the first instance only and rejects remote clients. --baseline reads HEAD.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
    return (ROOT / path).read_text(encoding='utf-8')


protocol = read('src/platform/windows/playnite_protocol.cpp')
pipes = read('src/platform/windows/ipc/pipes.cpp')
parse = protocol[protocol.index('Message parse(std::span<const uint8_t> bytes)'):]
parse = parse[:parse.index('\n  }\n')]
catch = parse[parse.rindex('} catch (...) {'):]
create = pipes[pipes.index('CreateNamedPipeW('):]
create = create[:create.index(';')]
failures = 0
for ok, name in (
    ('m = Message {};' in catch, 'a parse failure resets the message instead of returning a partial snapshot'),
    ('FILE_FLAG_FIRST_PIPE_INSTANCE' in create, 'the Playnite pipe is created as the first instance only'),
    ('PIPE_REJECT_REMOTE_CLIENTS' in create, 'the Playnite pipe rejects remote clients'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
