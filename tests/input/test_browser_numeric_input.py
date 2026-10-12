#!/usr/bin/env python3
"""Run production browser gamepad/wheel parsing with real JSON and recorded packets.

Checks numeric saturation before integer conversion, fractional rounding, and
whole-message rejection. --baseline extracts the production branches from HEAD.
"""
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
#include <cerrno>
#include <cfenv>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>
constexpr uint8_t LI_CTYPE_UNKNOWN=0, LI_CTYPE_XBOX=1, LI_CTYPE_PS=2, LI_CTYPE_NINTENDO=3;
struct packet { std::string type; std::vector<int> values; };
std::vector<packet> sent;
packet make_gamepad_arrival_packet(int, uint8_t, uint16_t, uint32_t) { return {"arrival",{}}; }
packet make_gamepad_state_packet(int, uint16_t, uint32_t, uint8_t lt, uint8_t rt,
                                int16_t lx, int16_t ly, int16_t rx, int16_t ry) {
  return {"state",{lt,rt,lx,ly,rx,ry}};
}
packet make_scroll_packet(int amount) { return {"vertical",{std::clamp(amount,-32768,32767)}}; }
packet make_hscroll_packet(int amount) { return {"horizontal",{std::clamp(amount,-32768,32767)}}; }
namespace input { void passthrough(int, packet p, int) { sent.push_back(p); } }
std::mutex gamepad_mutex;
std::bitset<16> webrtc_gamepads;
@@HELPERS@@
void handle(const nlohmann::json &message) {
  auto type=message.value("type", ""); int input_ctx=0, input_permission=0;
  @@STATE@@
  @@WHEEL@@
}
void receive(const nlohmann::json &message) {
  try { handle(message); } catch (const std::exception &) {} // production callback boundary
}
int checks, failures;
void check(bool ok, const char *name) {
  ++checks; failures+=!ok; std::cout<<(ok?"PASS ":"FAIL ")<<name<<'\n';
}
void reset() { sent.clear(); webrtc_gamepads.reset(); std::feclearexcept(FE_ALL_EXCEPT); errno=0; }
int main() {
  using nlohmann::json;
  for (double magnitude : {2147483648.0,4294967296.0,1e20,1e300,std::numeric_limits<double>::max()}) {
    reset();
    // Parse the same on-wire JSON numbers the data-channel callback receives.
    receive(json::parse(json{{"type","gamepad_state"},{"id",0},{"activeMask",1},
      {"lt",magnitude},{"rt",-magnitude},{"lsX",magnitude},{"lsY",-magnitude},
      {"rsX",magnitude},{"rsY",-magnitude}}.dump()));
    check(sent.size()==2 && sent[1].values==std::vector<int>({255,0,32767,-32768,32767,-32768}),
          "large finite axes and triggers saturate in their original direction");
    check(!(std::fetestexcept(FE_INVALID)) && errno!=EDOM,
          "numeric conversion does not raise a rounding domain error");
    reset(); receive(json{{"type","wheel"},{"dx",magnitude},{"dy",magnitude}});
    check(sent.size()==2 && sent[0].type=="vertical" && sent[0].values[0]==-32768 &&
          sent[1].type=="horizontal" && sent[1].values[0]==32767,
          "large positive wheel deltas retain both directions");
    reset(); receive(json{{"type","wheel"},{"dx",-magnitude},{"dy",-magnitude}});
    check(sent.size()==2 && sent[0].values[0]==32767 && sent[1].values[0]==-32768,
          "large negative wheel deltas retain both directions");
  }
  reset(); receive(json{{"type","gamepad_state"},{"id",0},{"activeMask",1},
    {"lt",127.5},{"rt",254.49},{"lsX",-1.5},{"lsY",1.5},{"rsX",32767},{"rsY",-32768}});
  check(sent.size()==2 && sent[1].values==std::vector<int>({128,254,-2,2,32767,-32768}),
        "ordinary fractional rounding and signed endpoints stay unchanged");
  reset(); receive(json{{"type","wheel"},{"dx",0.0125},{"dy",-0.0125}});
  check(sent.size()==2 && sent[0].values[0]==2 && sent[1].values[0]==2, "fractional wheel units round to protocol units");
  reset(); receive(json{{"type","wheel"}});
  check(sent.empty(), "missing wheel deltas remain zero");
  for (const auto &value : std::vector<json>{nullptr,true,"invalid",json::array()}) {
    reset(); receive(json{{"type","gamepad_state"},{"id",0},{"lt",123},{"rsY",value}});
    check(sent.empty() && webrtc_gamepads.none(), "malformed final axis cannot send an arrival or partial state");
    reset(); receive(json{{"type","wheel"},{"dx",1},{"dy",value}});
    check(sent.empty(), "malformed second wheel axis cannot send a partial scroll");
  }
  std::cout<<checks<<" checks; "<<failures<<" failures\n";
  return failures!=0;
}
'''
helpers = ['    std::optional<std::int64_t> json_integer(', '    uint8_t parse_gamepad_type(']
# The baseline has no safe numeric conversion helpers yet.
helpers += [m for m in ['    double json_finite_number(', '    int round_clamped_input('] if m in source]
code = code.replace('@@HELPERS@@', '\n'.join(block(m) for m in helpers))
code = code.replace('@@STATE@@', block('      if (type == "gamepad_state")'))
code = code.replace('@@WHEEL@@', block('      if (type == "wheel")'))
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code)
    command = ['g++', '-std=c++20', '-g', '-fsanitize=address,undefined,float-cast-overflow', '-fno-omit-frame-pointer']
    if os.environ.get('AUDIT_JSON_INCLUDE'):
        command += ['-I', os.environ['AUDIT_JSON_INCLUDE']]
    subprocess.run(command + [str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
