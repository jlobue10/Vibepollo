#!/usr/bin/env python3
"""Text gate for the WebRTC session-count cap: at most remote_session::max_client_vdds * 2
registered sessions, checked atomically with the registration under session_mutex, refused
(never evicted) with a distinct error that the HTTP handler maps to 503 + Retry-After instead
of tearing every session down as "shutdown in progress". --baseline reads HEAD.
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


webrtc = read('src/webrtc_stream.cpp')
header = read('src/webrtc_stream.h')
confighttp = read('src/confighttp.cpp')
create = block(webrtc, 'std::optional<SessionState> create_session(')
lock = create.index('std::lock_guard lg {session_mutex};')
failures = 0
for ok, name in (
    ('constexpr std::size_t kMaxWebRtcSessions = remote_session::max_client_vdds * 2;' in webrtc, 'the cap is the shared RTSP launch bound (max_client_vdds * 2)'),
    ('sessions.size() >= kMaxWebRtcSessions' in create and lock < create.index('sessions.size() >= kMaxWebRtcSessions') < create.index('sessions.emplace('), 'the cap is checked under session_mutex, before the registration'),
    ('create_error_e::limit' in create and 'create_error_e::limit' in confighttp, 'a refused session carries a distinct error to the HTTP handler'),
    ('enum class create_error_e' in header, 'the error enum is part of the public interface'),
    ('create_error_e::limit' in confighttp and confighttp.index('create_error_e::limit') < confighttp.index('shutdown_all_sessions();', confighttp.index('create_error_e::limit')),
     'the HTTP handler answers the cap before the shutdown path (503 + Retry-After, no teardown)'),
    ('headers.emplace("Retry-After", "5")' in confighttp, 'the refusal carries Retry-After'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
