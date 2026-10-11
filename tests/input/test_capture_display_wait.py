#!/usr/bin/env python3
"""Run the production capture_async loop head while the capture thread has not yet
selected a display: the encoder thread must wait between polls, not spin a core.
--baseline runs the source at HEAD (hundreds of thousands of iterations in 200 ms).
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
baseline = '--baseline' in sys.argv
source = (subprocess.check_output(['git', 'show', 'HEAD:src/video.cpp'], cwd=ROOT, text=True, encoding='utf-8')
          if baseline else (ROOT / 'src/video.cpp').read_text(encoding='utf-8'))

start = source.index('    while (!shutdown_event->peek() && images->running()) {')
end = source.index('      if (config.videoFormat == 3) {', start)
loop_head = source[start:end]
code = r"""
#include <chrono>
#include <iostream>
#include <memory>
#include <thread>
using namespace std::literals;
namespace platf { struct display_t {}; }
struct event_t { bool value {false}; bool peek() const { return value; } };
struct images_t {
  std::chrono::steady_clock::time_point until;
  bool running() const { return std::chrono::steady_clock::now() < until; }
};
struct guard_t {};
struct weak_display_t {
  bool expired() const { return true; }
  std::shared_ptr<platf::display_t> lock() const { return nullptr; }
};
struct display_slot_t {
  int polls {0};
  weak_display_t inner;
  guard_t lock() { ++polls; return {}; }
  weak_display_t *operator->() { return &inner; }
};
struct source_ctx_t { event_t reinit_event; display_slot_t display_wp; };
int main() {
  event_t shutdown_storage; event_t *shutdown_event = &shutdown_storage;
  images_t images_storage {std::chrono::steady_clock::now() + 200ms}; images_t *images = &images_storage;
  source_ctx_t source_ctx;
  LOOP_HEAD
      // (the loop body after the display wait is not part of this harness)
    }
  std::cout << "polls in 200 ms: " << source_ctx.display_wp.polls << '\n';
  const bool ok = source_ctx.display_wp.polls >= 2 && source_ctx.display_wp.polls <= 40;
  std::cout << (ok ? "PASS" : "FAIL") << " the encoder thread polls for a display at the 20 ms cadence\n";
  return ok ? 0 : 1;
}
"""
code = code.replace('LOOP_HEAD', loop_head)
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code, encoding='utf-8')
    subprocess.run(['g++', '-std=c++20', '-g', '-pthread', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    result = subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'), timeout=30)
    raise SystemExit(result.returncode != 0)
