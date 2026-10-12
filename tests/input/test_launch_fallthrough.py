#!/usr/bin/env python3
"""Text gate for the /launch request path.
H26-02: make_launch_session_from_snapshot() derives the client cipher/IV once, inside the
paired-client block; a second unconditional get_arg(args, "rikeyid") turned every web-UI launch
(no query) into a 400. H26-03: the "How did you get here?" 403 (appid <= 0 and no appuuid)
returns instead of being overwritten by the 200 that starts a raw desktop stream. H26-04: a
dummy-frame convert failure is a runtime failure for non-native encoders. --baseline reads HEAD.
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


nvhttp = read('src/nvhttp.cpp')
video = read('src/video.cpp')
snapshot = block(nvhttp, 'static std::shared_ptr<rtsp_stream::launch_session_t> make_launch_session_from_snapshot(')
client_block = block(snapshot, 'if (launch_session->client_uuid != http::unique_id) {')
forbidden = nvhttp.index('"How did you get here?"')
after_forbidden = nvhttp[forbidden:nvhttp.index('status_code", 200)', forbidden)]
dummy = video[video.index('session->convert(*dummy_img)'):]
dummy = dummy[:dummy.index('\n      }\n')]
failures = 0
for ok, name in (
    (snapshot.count('get_arg(args, "rikeyid")') == 1 and 'get_arg(args, "rikeyid")' in client_block,
     'the launch IV is derived once, only for a paired client (a web-UI launch has no rikeyid)'),
    (snapshot.count('launch_session->iv.resize(16)') == 1, 'the launch IV block is not duplicated'),
    ('return;' in after_forbidden, 'a launch that names no app returns its 403 instead of streaming the desktop'),
    ('runtime_failed' in dummy, 'a first-frame convert failure is a runtime failure (no counter-reset hot loop)'),
    (nvhttp.count('if (launched_here) proc::proc.terminate(') == 2 and nvhttp.count('if (appid > 0) proc::proc.terminate(') == 0,
     'a refused admission terminates only an app this request launched'),
    ('util::from_view(appid_str) <= 0' in nvhttp and nvhttp.index('util::from_view(appid_str) <= 0') < nvhttp.index('remote_session::identify(util::from_view(appid_str)'),
     'a launch naming no app is refused at the request boundary (400 before any host work)'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
