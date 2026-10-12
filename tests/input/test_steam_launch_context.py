#!/usr/bin/env python3
"""Text gate: the web Steam launch route goes through platf::run_command on Windows (interactive
user token when the host is a service, system-first resolver, no inherited handles), never
ShellExecuteW in the host's own context. --baseline reads HEAD.
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


failures = 0


def check(ok, name):
    global failures
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok


steam = read('src/steam_integration.cpp')
launch = block(steam, 'bool launch(std::uint32_t app_id)')
windows = launch[launch.index('#ifdef _WIN32'):launch.index('#else')]
check('ShellExecuteW(' not in steam and 'ShellExecuteExW(' not in steam, 'steam_integration.cpp never launches through ShellExecute')
check('platf::run_command(false, false, launch_command(app_id)' in windows, 'the Windows launch runs launch_command(app_id) through platf::run_command')
check('child.detach()' in windows and windows.index('if (ec)') < windows.index('child.detach()'), 'a failed opener is reported before the child is detached')
check(', nullptr, ec, nullptr)' in windows, 'no log handle or process group is attached to the opener')
sys.exit(1 if failures else 0)
