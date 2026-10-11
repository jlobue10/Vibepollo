#!/usr/bin/env python3
"""Exercise the production Windows pointer mapping and injection-failure handling without Win32.

POINTER_INFO::ptPixelLocation is screen-relative, the touch port offset is virtual-desktop-relative
(display_base subtracts SM_XVIRTUALSCREEN for the mouse's MOUSEEVENTF_VIRTUALDESK), so taps used to
land shifted whenever a monitor sat left of or above the primary. A rejected
InjectSyntheticPointerInput used to leave the edge flags set, poisoning every later frame.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src/platform/windows/input.cpp'


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


PRELUDE = r'''
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string_view>
using namespace std::literals;
using LONG = long;
using UINT32 = unsigned;
int fake_virtual_x = 0, fake_virtual_y = 0;
constexpr int SM_XVIRTUALSCREEN = 76, SM_YVIRTUALSCREEN = 77;
int GetSystemMetrics(int index) { return index == SM_XVIRTUALSCREEN ? fake_virtual_x : fake_virtual_y; }
#define BOOST_LOG(level) std::cerr
struct POINT { LONG x, y; };
struct POINTER_INFO { UINT32 pointerFlags = 0; POINT ptPixelLocation {}; };
constexpr UINT32 POINTER_FLAG_NONE = 0, POINTER_FLAG_INRANGE = 0x2, POINTER_FLAG_INCONTACT = 0x4,
                 POINTER_FLAG_CANCELED = 0x8000, POINTER_FLAG_DOWN = 0x10000, POINTER_FLAG_UPDATE = 0x20000,
                 POINTER_FLAG_UP = 0x40000;
constexpr uint8_t LI_TOUCH_EVENT_HOVER = 0, LI_TOUCH_EVENT_DOWN = 1, LI_TOUCH_EVENT_UP = 2, LI_TOUCH_EVENT_MOVE = 3,
                  LI_TOUCH_EVENT_CANCEL = 4, LI_TOUCH_EVENT_BUTTON_ONLY = 5, LI_TOUCH_EVENT_HOVER_LEAVE = 6,
                  LI_TOUCH_EVENT_CANCEL_ALL = 7;
namespace platf {
  struct touch_port_t { int offset_x, offset_y, width, height; };
'''

TESTS = r'''
}  // namespace platf
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  const platf::touch_port_t left_monitor {0, 0, 1920, 1080}, primary {1920, 0, 1920, 1080};

  fake_virtual_x = -1920;
  POINTER_INFO p {};
  platf::populate_common_pointer_info(p, left_monitor, LI_TOUCH_EVENT_DOWN, 0.5f, 0.5f);
  check(p.ptPixelLocation.x == -960 && p.ptPixelLocation.y == 540, "monitor_left_of_primary: a centre tap lands at negative screen x");
  POINTER_INFO q {};
  platf::populate_common_pointer_info(q, primary, LI_TOUCH_EVENT_MOVE, 0.5f, 0.5f);
  check(q.ptPixelLocation.x == 960 && q.ptPixelLocation.y == 540, "primary_with_monitor_left: a centre tap lands on the primary");

  fake_virtual_x = 0;
  POINTER_INFO r {};
  platf::populate_common_pointer_info(r, left_monitor, LI_TOUCH_EVENT_HOVER, 0.25f, 0.25f);
  check(r.ptPixelLocation.x == 480 && r.ptPixelLocation.y == 270, "single monitor mapping is unchanged");

  POINTER_INFO s {};
  platf::populate_common_pointer_info(s, left_monitor, LI_TOUCH_EVENT_DOWN, 0.5f, 0.5f);
  platf::note_pointer_injection_failure(s, LI_TOUCH_EVENT_DOWN);
  check(s.pointerFlags == (POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT), "a rejected DOWN keeps the contact without the edge flag");
  platf::populate_common_pointer_info(s, left_monitor, LI_TOUCH_EVENT_MOVE, 0.6f, 0.6f);
  check((s.pointerFlags & POINTER_FLAG_DOWN) == 0 && (s.pointerFlags & POINTER_FLAG_UPDATE), "the following MOVE is a plain UPDATE");
  platf::populate_common_pointer_info(s, left_monitor, LI_TOUCH_EVENT_UP, 0.0f, 0.0f);
  platf::note_pointer_injection_failure(s, LI_TOUCH_EVENT_UP);
  check(s.pointerFlags == POINTER_FLAG_NONE, "a rejected UP frees the slot");

  POINTER_INFO pen {};
  platf::populate_common_pointer_info(pen, left_monitor, LI_TOUCH_EVENT_DOWN, 0.5f, 0.5f);
  platf::populate_common_pointer_info(pen, {}, LI_TOUCH_EVENT_CANCEL_ALL, 0.0f, 0.0f);
  platf::note_pointer_injection_failure(pen, LI_TOUCH_EVENT_CANCEL_ALL);
  check(pen.pointerFlags == POINTER_FLAG_NONE, "a rejected pen CANCEL_ALL leaves no CANCELED|UP residue");
  return failures ? 1 : 0;
}
'''


def main():
    source = SRC.read_text(encoding='utf-8')
    edge = re.search(r'constexpr auto EDGE_TRIGGERED_POINTER_FLAGS = [^;]+;', source)
    try:
        body = '\n'.join([edge.group() if edge else '',
                          function(source, 'void populate_common_pointer_info('),
                          function(source, 'void note_pointer_injection_failure(')])
    except ValueError as error:
        print('FAIL: production helper missing:', error)
        return 1
    with tempfile.TemporaryDirectory(prefix='windows-pointer-offset-') as temp:
        cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
        cpp.write_text(PRELUDE + body + TESTS, encoding='utf-8')
        subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-Wno-unused-parameter',
                        str(cpp), '-o', str(binary)], check=True)
        return subprocess.run([str(binary)]).returncode


if __name__ == '__main__':
    sys.exit(main())
