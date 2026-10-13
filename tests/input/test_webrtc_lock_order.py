#!/usr/bin/env python3
"""Check the lock order between session_mutex and webrtc_capture.mutex.

The encoder and media threads hold session_mutex while a dependency gap reaches
request_keyframe() -> current_capture_mail(), which takes webrtc_capture.mutex.
Any scope that takes the capture mutex and then session_mutex inside it inverts
that order and can deadlock session creation against a frame drop. This walks
the brace structure of src/webrtc_stream.cpp (comments and literals removed) and
reports every lock_guard/unique_lock/scoped_lock of session_mutex that sits
inside a scope still holding webrtc_capture.mutex. No compiler needed.
--baseline reads HEAD's production code.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/webrtc_stream.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/webrtc_stream.cpp').read_text())


def blank(match):
    return re.sub(r'[^\n]', ' ', match.group())


# Keep line numbers: comments, string and character literals become spaces.
stripped = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])\'', blank, source, flags=re.S)

LOCK = r'\b(?:std::)?(?:lock_guard|unique_lock|scoped_lock)\s*(?:<[^;()]*?>)?\s*(\w+)\s*\(\s*{}\s*\)'
capture_lock = re.compile(LOCK.format(r'webrtc_capture\.mutex'))
session_lock = re.compile(LOCK.format(r'session_mutex'))
unlock = re.compile(r'\b(\w+)\s*\.\s*unlock\s*\(\s*\)')
checks = 0
violations = []
capture_mail_locks_capture = False


class Scope:
    def __init__(self):
        self.text = ''  # statement text accumulated at this level
        self.capture = None  # name of the capture lock variable held by this scope
        self.session = False


scopes = [Scope()]
line = 1


def statement(scope_stack, text, at_line):
    global checks, capture_mail_locks_capture
    text = text.strip()
    if not text:
        return
    scope = scope_stack[-1]
    if m := capture_lock.search(text):
        scope.capture = m.group(1)
        if any('current_capture_mail' in s.text for s in scope_stack):
            capture_mail_locks_capture = True
    if session_lock.search(text):
        checks += 1
        holders = [s.capture for s in scope_stack if s.capture]
        if holders:
            violations.append((at_line, holders[-1]))
        scope.session = True
    if m := unlock.search(text):
        for s in scope_stack:
            if s.capture == m.group(1):
                s.capture = None


for ch in stripped:
    if ch == '\n':
        line += 1
    if ch == '{':
        scopes.append(Scope())
        continue
    if ch == '}':
        inner = scopes.pop()
        if ';' not in inner.text:
            # A brace initialiser such as `lock_guard lg {session_mutex}`: fold it
            # into the enclosing statement as a call so the lock regex sees it.
            scopes[-1].text += '(' + inner.text + ')'
        continue
    if ch == ';':
        statement(scopes, scopes[-1].text, line)
        scopes[-1].text = ''
        continue
    scopes[-1].text += ch

if len(scopes) != 1:
    print(f'FAIL unbalanced braces (depth {len(scopes) - 1} at end of file)')
    sys.exit(1)
if not capture_mail_locks_capture:
    print('FAIL current_capture_mail() no longer takes webrtc_capture.mutex; update this check to the new inner lock')
    sys.exit(1)
for at_line, holder in violations:
    print(f'FAIL src/webrtc_stream.cpp:{at_line}: session_mutex taken while {holder} holds webrtc_capture.mutex '
          '(encoder/media threads take session_mutex first and reach the capture mutex through request_keyframe)')
print(f'{checks} session_mutex lock sites checked, {len(violations)} nested inside webrtc_capture.mutex')
sys.exit(bool(violations))
