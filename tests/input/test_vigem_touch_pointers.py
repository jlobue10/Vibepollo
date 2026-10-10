#!/usr/bin/env python3
"""Execute the production ViGEm DS4 touchpad pointer bookkeeping with a fake ViGEm target.

A repeated DOWN for a pointer whose UP the client dropped must keep that pointer's
contact instead of taking the second index and leaving the first finger pressed.
No ViGEmBus or controller is required; --baseline reads the Windows input.cpp from HEAD.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'src/platform/windows/input.cpp'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())

def block(text, marker):
    start = text.index(marker)
    end = text.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (text[end] == '{') - (text[end] == '}')
    return text[start:end + 1]

prefix = r"""
#include <cstdint>
#include <cstring>
#include <iostream>
#include <map>
#include <memory>
#include <string_view>
using namespace std::literals;
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) Log{}
constexpr int LI_TOUCH_EVENT_HOVER = 0, LI_TOUCH_EVENT_DOWN = 1, LI_TOUCH_EVENT_UP = 2, LI_TOUCH_EVENT_MOVE = 3,
              LI_TOUCH_EVENT_CANCEL = 4, LI_TOUCH_EVENT_CANCEL_ALL = 7;
enum VIGEM_TARGET_TYPE { Xbox360Wired, DualShock4Wired };
struct DS4_TOUCH { uint8_t bPacketCounter, bIsUpTrackingNum1, bTouchData1[3], bIsUpTrackingNum2, bTouchData2[3]; };
struct DS4_REPORT_EX { struct { DS4_TOUCH sCurrentTouch; } Report; };
namespace platf {
  struct gamepad_id_t { int globalIndex, clientRelativeIndex; };
  struct gamepad_touch_t { gamepad_id_t id; uint8_t eventType; uint8_t touchpadIndex; uint32_t pointerId; float x, y, pressure; };
  struct gamepad_context_t {
    std::shared_ptr<int> gp = std::make_shared<int>(1);
    union { DS4_REPORT_EX ds4; } report {};
    std::map<uint64_t, uint8_t> pointer_id_map;
    uint8_t available_pointers = 0x3;
  };
  struct vigem_t { gamepad_context_t gamepads[16]; int sends = 0; };
  struct vhf_t { void touch(int, const gamepad_touch_t &) {} };
  struct input_raw_t { vigem_t *vigem = nullptr; vhf_t *vhf = nullptr; };
  using input_t = std::shared_ptr<input_raw_t>;
  bool vhf_owns_gamepad(input_raw_t *, int) { return false; }
  VIGEM_TARGET_TYPE vigem_target_get_type(int *) { return DualShock4Wired; }
  void ds4_update_ts_and_send(vigem_t *vigem, int) { ++vigem->sends; }
"""
suffix = r"""
}
int main() {
  using namespace platf;
  int failures = 0, checks = 0;
  auto check = [&](bool ok, const char *name) { ++checks; failures += !ok; std::cout << (ok ? "PASS " : "FAIL ") << name << '\n'; };
  vigem_t vigem;
  auto input = std::make_shared<input_raw_t>();
  input->vigem = &vigem;
  auto &g = vigem.gamepads[0];
  auto &touch = g.report.ds4.Report.sCurrentTouch;
  touch.bIsUpTrackingNum1 = touch.bIsUpTrackingNum2 = 0x80;
  gamepad_touch_t down {{0, 0}, LI_TOUCH_EVENT_DOWN, 0, 7, 0.5f, 0.5f, 1.0f};
  gamepad_touch(input, down);
  check(g.pointer_id_map.size() == 1 && g.available_pointers == 0x2 && !(touch.bIsUpTrackingNum1 & 0x80), "a down takes the first index");
  gamepad_touch(input, down);
  check(g.pointer_id_map.size() == 1 && g.pointer_id_map.begin()->second == 0 && g.available_pointers == 0x2 && (touch.bIsUpTrackingNum2 & 0x80),
        "a repeated down keeps the pointer's first contact instead of taking the second index");
  gamepad_touch_t up = down;
  up.eventType = LI_TOUCH_EVENT_UP;
  gamepad_touch(input, up);
  check(g.available_pointers == 0x3 && g.pointer_id_map.empty() && (touch.bIsUpTrackingNum1 & 0x80) && (touch.bIsUpTrackingNum2 & 0x80),
        "the up releases the contact and leaves no phantom finger");
  gamepad_touch_t second = down;
  second.pointerId = 8;
  gamepad_touch(input, down);
  gamepad_touch(input, second);
  check(g.available_pointers == 0 && g.pointer_id_map.size() == 2, "two distinct pointers take both indices");
  gamepad_touch_t cancel_all = down;
  cancel_all.eventType = LI_TOUCH_EVENT_CANCEL_ALL;
  gamepad_touch(input, cancel_all);
  check(g.available_pointers == 0x3 && g.pointer_id_map.empty(), "cancel-all releases everything");
  check(vigem.sends == 6, "every accepted event (the repeated down included) reaches the target");
  std::cout << checks << " checks; " << failures << " failures\n";
  return failures ? 1 : 0;
}
"""
code = prefix + block(source, 'void gamepad_touch(input_t &input, const gamepad_touch_t &touch)') + suffix
with tempfile.TemporaryDirectory(prefix='vigem-touch-') as directory:
    work = Path(directory)
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
