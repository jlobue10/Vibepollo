#!/usr/bin/env python3
"""Text gate for the browser-facing WebRTC inputs: the session options parser bounds width, height,
fps, bitrate and audio channels (they reach the encoder, the virtual display and framerate * 100
unchanged), stored ICE candidates are capped per session, and the input message handler reads
integers through json_integer() (integer type and range) instead of float-to-int conversions.
--baseline reads HEAD.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
    return (ROOT / path).read_text(encoding='utf-8')


confighttp = read('src/confighttp.cpp')
webrtc = read('src/webrtc_stream.cpp')
failures = 0
for ok, name in (
    ('bounded("width", 16, 8192)' in confighttp and 'bounded("height", 16, 8192)' in confighttp, 'WebRTC width/height are bounded 16..8192'),
    ('bounded("fps", 10, 1000)' in confighttp, 'WebRTC fps is bounded 10..1000'),
    ('bounded("bitrate_kbps", 0, 500000)' in confighttp, 'WebRTC bitrate is bounded 0..500000 kbps'),
    ('channels != 2 && channels != 6 && channels != 8' in confighttp, 'WebRTC audio channels are 2, 6 or 8'),
    ('kMaxStoredCandidates' in webrtc and 'kMaxCandidateLength' in webrtc, 'stored ICE candidates are capped per session'),
    (re.search(r'message\.value\("[A-Za-z]+", -?\d+\)', webrtc) is None, 'no browser integer is read through a float-to-int message.value()'),
    (webrtc.count('json_integer(message,') >= 8, 'input message integers go through json_integer()'),
    ('bounded("app_id", 0,' in confighttp and 'bounded("video_pacing_slack_ms"' in confighttp and 'bounded("video_max_frame_age_ms"' in confighttp,
     'WebRTC app_id and pacing options are integer-checked through bounded()'),
    ('profile.size() > 64' in confighttp, 'the WebRTC profile string is capped'),
    (webrtc.index('json_integer(message, "button"') < webrtc.index('make_abs_mouse_move_packet(x, y)', webrtc.index('if (type == "mouse_down" || type == "mouse_up")')),
     'mouse_down/mouse_up parses the button before dispatching the absolute move'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
