#!/usr/bin/env python3
"""Execute the production deferred stream-start poll against a held lifecycle gate.

The control-broadcast thread runs this every iteration; a launch or teardown that owns the
process-wide lifecycle mutex may be waiting for that thread's controlEnd, so the poll must
never block on the gate (it used to, and the 10 s join watchdog then killed the process).
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'src/stream.cpp'
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
#include <chrono>
#include <future>
#include <iostream>
#include <mutex>
#include <optional>
#include <string_view>
using namespace std::literals;
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) Log{}
namespace nvhttp { std::mutex &stream_lifecycle_mutex() { static std::mutex m; return m; } }
namespace platf {
  enum class frame_limiter_owner { rtsp };
  struct policy_t { int value; };
  int limiter_starts = 0;
  void frame_limiter_streaming_start(frame_limiter_owner, const policy_t &) { ++limiter_starts; }
}
namespace stream {
  struct deferred_stream_start_t { std::optional<platf::policy_t> policy; };
  std::mutex &deferred_stream_start_mutex() { static std::mutex m; return m; }
  std::optional<deferred_stream_start_t> &deferred_stream_start_state() { static std::optional<deferred_stream_start_t> s; return s; }
  bool ready = true, still_needed = true;
  bool user_session_ready() { return ready; }
  bool rtsp_stream_start_actions_still_needed() { return still_needed; }
  namespace session { int shared_starts = 0; void start_shared_platform_if_needed() { ++shared_starts; } }
"""
suffix = r"""
}
int main() {
  using namespace stream;
  int failures = 0, checks = 0;
  auto check = [&](bool ok, const char *name) { ++checks; failures += !ok; std::cout << (ok ? "PASS " : "FAIL ") << name << '\n'; };
  deferred_stream_start_state() = deferred_stream_start_t {platf::policy_t {7}};
  {
    // A launch/teardown owns the gate: the poll must return promptly and keep the deferred state.
    std::unique_lock<std::mutex> held(nvhttp::stream_lifecycle_mutex());
    auto result = std::async(std::launch::async, [] { return apply_deferred_stream_start_actions_if_ready(); });
    const bool prompt = result.wait_for(std::chrono::milliseconds(500)) == std::future_status::ready;
    check(prompt, "the poll does not block on a held lifecycle gate");
    if (prompt) {
      check(result.get() == false && deferred_stream_start_state().has_value() && platf::limiter_starts == 0,
            "a held gate leaves the deferred state for the next iteration");
    } else {
      held.unlock();
      result.get();
    }
  }
  check(apply_deferred_stream_start_actions_if_ready() == true && !deferred_stream_start_state().has_value() &&
        platf::limiter_starts == 1 && session::shared_starts == 1, "a free gate applies the deferred actions once");
  check(apply_deferred_stream_start_actions_if_ready() == false, "nothing to apply afterwards");
  deferred_stream_start_state() = deferred_stream_start_t {};
  ready = false;
  check(apply_deferred_stream_start_actions_if_ready() == false && deferred_stream_start_state().has_value(),
        "no user session: state kept");
  ready = true; still_needed = false;
  check(apply_deferred_stream_start_actions_if_ready() == false && !deferred_stream_start_state().has_value() && platf::limiter_starts == 1,
        "no active RTSP stream: state consumed without actions");
  std::cout << checks << " checks; " << failures << " failures\n";
  return failures ? 1 : 0;
}
"""
code = prefix + block(source, 'bool apply_deferred_stream_start_actions_if_ready()') + suffix
with tempfile.TemporaryDirectory(prefix='lifecycle-gate-') as directory:
    work = Path(directory)
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-g', '-pthread', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
