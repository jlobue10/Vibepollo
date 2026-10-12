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


def block(source, marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


confighttp = read('src/confighttp.cpp')
webrtc = read('src/webrtc_stream.cpp')
failures = 0
for ok, name in (
    ('bounded("width", kMinWebRtcDimension, kMaxWebRtcDimension)' in confighttp and 'bounded("height", kMinWebRtcDimension, kMaxWebRtcDimension)' in confighttp,
     'WebRTC width/height are bounded by the advertised constants'),
    ('bounded("fps", kMinWebRtcFps, kMaxWebRtcFps)' in confighttp, 'WebRTC fps is bounded by the advertised constants'),
    ('bounded("bitrate_kbps", 0, kAbsoluteMaxWebRtcBitrateKbps)' in confighttp, 'WebRTC bitrate is bounded by the absolute maximum'),
    (confighttp.count('constexpr int kMaxWebRtcDimension') == 1 and 'constexpr int kMaxWebRtcDimension = 8192;' in confighttp and 'constexpr int kMinWebRtcFps = 10;' in confighttp,
     'one constant set, advertising what the parser enforces (64..8192 even, fps 10..1000)'),
    ('{"min_dimension", kMinWebRtcDimension}' in confighttp and '{"min_fps", kMinWebRtcFps}' in confighttp, 'the capabilities endpoint advertises the enforced limits'),
    (webrtc.count('json_finite_number(message, "x")') >= 1 and webrtc.count('json_finite_number(message, "y")') >= 1, 'mouse_move coordinates are finite numbers'),
    ('channels != 2 && channels != 6 && channels != 8' in confighttp, 'WebRTC audio channels are 2, 6 or 8'),
    ('kMaxStoredCandidates' in webrtc and 'kMaxCandidateLength' in webrtc, 'stored ICE candidates are capped per session'),
    (re.search(r'message\.value\("[A-Za-z]+", -?\d+\)', webrtc) is None, 'no browser integer is read through a float-to-int message.value()'),
    (webrtc.count('json_integer(message,') >= 8, 'input message integers go through json_integer()'),
    ('bounded("app_id", 0,' in confighttp and 'bounded("video_pacing_slack_ms"' in confighttp and 'bounded("video_max_frame_age_ms"' in confighttp,
     'WebRTC app_id and pacing options are integer-checked through bounded()'),
    ('profile.size() > 64' in confighttp, 'the WebRTC profile string is capped'),
    ('kMaxOfferSdpBytes' in block(confighttp, 'void postWebRTCOffer(') and
     block(confighttp, 'void postWebRTCOffer(').index('kMaxOfferSdpBytes') < block(confighttp, 'void postWebRTCOffer(').index('set_remote_offer('),
     'the remote offer SDP is bounded and type-checked before it reaches libwebrtc'),
    ('substr(0, kLoggedSdpBytes)' in block(webrtc, 'void on_set_remote_failure(') and '<< offer_sdp;' not in block(webrtc, 'void on_set_remote_failure('),
     'a rejected offer is logged truncated, never in full'),
    (webrtc.index('json_integer(message, "button"') < webrtc.index('make_abs_mouse_move_packet(x, y)', webrtc.index('if (type == "mouse_down" || type == "mouse_up")')),
     'mouse_down/mouse_up parses the button before dispatching the absolute move'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
