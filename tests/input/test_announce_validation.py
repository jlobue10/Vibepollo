#!/usr/bin/env python3
"""Compile the production ANNOUNCE field policy and check the packetSize/packetDuration bounds.

A packetSize of 16 divided the video broadcast thread by zero and smaller values wrote past
the FEC shard buffer; a packetDuration outside Opus' frame sizes stopped the process-wide
audio queue (ending every session) or overflowed the frame-size arithmetic. No sockets.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'src/rtsp_pending_policy.cpp'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())
header = (subprocess.check_output(['git', 'show', 'HEAD:src/rtsp_pending_policy.h'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/rtsp_pending_policy.h').read_text())

test = r"""
#include "rtsp_pending_policy.h"
#include <iostream>
using namespace rtsp_stream::pending_policy;
int main() {
  int failures = 0, checks = 0;
  auto check = [&](bool ok, const char *name) { ++checks; failures += !ok; std::cout << (ok ? "PASS " : "FAIL ") << name << '\n'; };
  check(parse_packet_size("1392") == 1392 && parse_packet_size("1024") == 1024, "usual packet sizes parse");
  check(parse_packet_size("200") == 200 && parse_packet_size("65535") == 65535, "the bounds are inclusive");
  for (const char *bad : {"16", "0", "-1", "199", "65536", "99999999999999999999", "abc", "", "1392x", " 1392", "+1392"}) {
    check(!parse_packet_size(bad), bad);
  }
  for (int ok : {5, 10, 20, 40, 60}) {
    check(parse_packet_duration(std::to_string(ok)) == ok, "an Opus frame duration parses");
  }
  for (const char *bad : {"7", "0", "-5", "44740", "2147483648", "5.0", "", "x"}) {
    check(!parse_packet_duration(bad), bad);
  }
  check(parse_bitrate_kbps("20000", false) == 20000 && parse_bitrate_kbps("800000", false) == 800000 && parse_bitrate_kbps("1", false) == 1,
        "bitrates within 1..800000 parse");
  check(parse_bitrate_kbps("0", true) == 0 && !parse_bitrate_kbps("0", false), "zero is accepted only where it means unconfigured");
  for (const char *bad : {"3000000000", "2147483647", "800001", "-1", "12abc", "", "99999999999999999999"}) {
    check(!parse_bitrate_kbps(bad, true) && !parse_bitrate_kbps(bad, false), bad);
  }
  std::cout << checks << " checks; " << failures << " failures\n";
  return failures ? 1 : 0;
}
"""
with tempfile.TemporaryDirectory(prefix='announce-validation-') as directory:
    work = Path(directory)
    (work / 'rtsp_pending_policy.h').write_text(header)
    # The policy header only needs remote_session::role_e; shadow the real header (which pulls in boost).
    (work / 'remote_session.h').write_text('#pragma once\nnamespace remote_session { enum class role_e : unsigned char { none, input, monitor, game }; }\n')
    (work / 'rtsp_pending_policy.cpp').write_text(source)
    (work / 'test.cpp').write_text(test)
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I' + str(work),
                    str(work / 'test.cpp'), str(work / 'rtsp_pending_policy.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
