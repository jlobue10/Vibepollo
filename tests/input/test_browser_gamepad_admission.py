#!/usr/bin/env python3
"""Rejected JSON must not commit browser gamepad state or emit partial packets."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/webrtc_stream.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/webrtc_stream.cpp').read_text())

def block(marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]

code = r'''
#include <nlohmann/json.hpp>
#include <algorithm>
#include <bitset>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
constexpr uint8_t LI_CTYPE_UNKNOWN=0, LI_CTYPE_XBOX=1, LI_CTYPE_PS=2, LI_CTYPE_NINTENDO=3;
struct packet { bool arrival; };
std::vector<packet> sent;
packet make_gamepad_arrival_packet(int, uint8_t, uint16_t, uint32_t) { return {true}; }
packet make_gamepad_state_packet(int, uint16_t, uint32_t, uint8_t, uint8_t, int16_t, int16_t, int16_t, int16_t) { return {false}; }
namespace input { void passthrough(int, packet p, int) { sent.push_back(p); } }
std::mutex gamepad_mutex;
std::bitset<16> webrtc_gamepads;
TYPE_PARSER
void handle(const nlohmann::json &message) {
    const auto type = message.value("type", "");
    const int input_ctx = 0, input_permission = 0;
    @@CONNECT@@
    @@STATE@@
    @@DISCONNECT@@
}
void receive(const nlohmann::json &message) {
    try { handle(message); } catch (const std::exception &) {} // libwebrtc callback boundary
}
int main() {
    using nlohmann::json;
    int checks = 0, failures = 0;
    auto check = [&](bool ok, const char *message) {
        ++checks; failures += !ok;
        std::cout << (ok ? "PASS " : "FAIL ") << message << '\n';
    };
    for (const char *type : {"gamepad_connect", "gamepad_state"}) {
        for (const char *field : {"capabilities", "supportedButtons"}) {
            webrtc_gamepads.reset(); sent.clear();
            receive(json{{"type",type},{"id",0},{field,"wrong type"}});
            check(!webrtc_gamepads.test(0) && sent.empty(), "rejected metadata leaves no registered gamepad or packet");
            receive(json{{"type","gamepad_connect"},{"id",0}});
            check(webrtc_gamepads.test(0) && sent.size()==1 && sent[0].arrival,
                  "a corrected connect still announces the gamepad");
        }
    }
    webrtc_gamepads.reset(); sent.clear();
    receive(json{{"type","gamepad_state"},{"id",0},{"lt","wrong type"}});
    check(!webrtc_gamepads.test(0) && sent.empty(), "rejected first state emits no partial arrival");
    receive(json{{"type","gamepad_state"},{"id",0},{"activeMask",1},{"lt",10}});
    check(webrtc_gamepads.test(0) && sent.size()==2 && sent[0].arrival && !sent[1].arrival,
          "valid first state announces once before input");
    sent.clear();
    receive(json{{"type","gamepad_connect"},{"id",0}});
    check(sent.empty(), "a duplicate valid connect sends no second arrival");
    receive(json{{"type","gamepad_disconnect"},{"id",0},{"activeMask","wrong type"}});
    check(webrtc_gamepads.test(0) && sent.empty(), "rejected disconnect preserves the existing registration");
    receive(json{{"type","gamepad_disconnect"},{"id",0},{"activeMask",0}});
    check(!webrtc_gamepads.test(0) && sent.size()==1 && !sent[0].arrival, "valid disconnect releases and unregisters");
    // A malformed active mask must never default to zero and remove live pads.
    for (const auto &value : std::vector<json>{nullptr, true, "bad", 1.5, -1, 65536,
            std::numeric_limits<std::uint64_t>::max()}) {
        webrtc_gamepads.set(0); sent.clear();
        receive(json{{"type","gamepad_disconnect"},{"id",0},{"activeMask",value}});
        check(webrtc_gamepads.test(0) && sent.empty(), "malformed disconnect cannot release a live gamepad");
        receive(json{{"type","gamepad_state"},{"id",0},{"activeMask",value}});
        check(webrtc_gamepads.test(0) && sent.empty(), "malformed state cannot publish a zero active mask");
    }
    for (const auto &value : std::vector<json>{nullptr, true, "bad", 1.5, -1, 65536,
            std::numeric_limits<std::uint64_t>::max()}) {
        webrtc_gamepads.reset(); sent.clear();
        receive(json{{"type","gamepad_connect"},{"id",0},{"capabilities",value}});
        check(!webrtc_gamepads.test(0) && sent.empty(), "malformed capabilities cannot poison the implicit registration");
    }
    webrtc_gamepads.reset(); sent.clear();
    receive(json{{"type","gamepad_connect"},{"id",0},{"capabilities",65535},{"supportedButtons",4294967295ULL}});
    check(webrtc_gamepads.test(0) && sent.size()==1 && sent[0].arrival,
          "maximum unsigned wire values remain valid");
    std::cout << checks << " checks; " << failures << " failures\n";
    return failures != 0;
}
'''
helpers = ['    std::optional<std::int64_t> json_integer(', '    uint8_t parse_gamepad_type(']
helpers += [m for m in ['    double json_finite_number(', '    int round_clamped_input('] if m in source]
code = code.replace('TYPE_PARSER', '\n'.join(block(m) for m in helpers))
for name in ['connect', 'state', 'disconnect']:
    code = code.replace('@@' + name.upper() + '@@', block('      if (type == "gamepad_' + name + '")'))
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code)
    command = ['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    if os.environ.get('AUDIT_JSON_INCLUDE'):
        command += ['-I', os.environ['AUDIT_JSON_INCLUDE']]
    subprocess.run(command + [str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
