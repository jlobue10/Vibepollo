#!/usr/bin/env python3
"""Run the production failed-start guard (session::start's live_session_guard) with counting fakes.

A start() that throws after the frame limiter was applied must release the limiter's
rtsp owner, or the next session's limiter start is ignored for the existing owner.
--baseline runs the guard text at HEAD.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
baseline = '--baseline' in sys.argv
source = (subprocess.check_output(['git', 'show', 'HEAD:src/stream.cpp'], cwd=ROOT, text=True, encoding='utf-8')
          if baseline else (ROOT / 'src/stream.cpp').read_text(encoding='utf-8'))

marker = 'auto live_session_guard = util::fail_guard([&]() {'
start = source.index(marker)
end = source.index('\n      });', start) + len('\n      });')
guard = source[start:end]
code = r"""
#include <atomic>
#include <cassert>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include "src/thread_safe.h"
#include "src/utility.h"
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) Log{}
namespace mail { constexpr std::string_view shutdown = "shutdown"; }
enum class state_e { STARTING, RUNNING, STOPPING };
struct session_t {
  std::atomic<state_e> state {state_e::RUNNING};
  safe::mail_t mail = std::make_shared<safe::mail_raw_t>();
  safe::mail_raw_t::event_t<bool> shutdown_event = mail->event<bool>(::mail::shutdown);
  std::string history_uuid {"uuid"};
};
int frame_limiter_sessions = 1, running_sessions = 1;
int limiter_stops = 0, deferred_clears = 0, stats_ended = 0, history_ended = 0, webrtc_inactive = 0;
namespace host_stats { void rtsp_session_ended() { ++stats_ended; } }
namespace session_history { void end_session(const std::string &) { ++history_ended; } }
namespace webrtc_stream { void set_rtsp_sessions_active(bool active) { if (!active) ++webrtc_inactive; } }
namespace platf {
  enum class frame_limiter_owner { rtsp, webrtc };
  void frame_limiter_streaming_stop(frame_limiter_owner owner, bool keep_rtss_running = false) { (void) keep_rtss_running; if (owner == frame_limiter_owner::rtsp) ++limiter_stops; }
}
void clear_deferred_stream_start_actions() { ++deferred_clears; }
int run(bool frame_limiter_counted, bool first_rtsp_session, bool first_frame_limiter_session, bool throws) {
  session_t session;
  try {
    GUARD
    if (throws) throw std::runtime_error("thread creation failed");
    live_session_guard.disable();
  } catch (const std::exception &) {}
  return session.state.load() == state_e::STOPPING;
}
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *what) { std::cout << (ok ? "PASS " : "FAIL ") << what << '\n'; failures += !ok; };
  // first session of the run, limiter applied, start throws afterwards
  frame_limiter_sessions = running_sessions = 1; limiter_stops = deferred_clears = stats_ended = history_ended = webrtc_inactive = 0;
  check(run(true, true, true, true) == 1 && frame_limiter_sessions == 0 && running_sessions == 0,
        "a throwing start unwinds the counters and marks the session STOPPING");
  check(limiter_stops == 1, "a throwing first session releases the frame limiter's rtsp owner");
#ifdef _WIN32
  check(deferred_clears == 1, "a throwing first session clears deferred stream-start actions");
#endif
  check(stats_ended == 1 && history_ended == 1 && webrtc_inactive == 1, "stats, history and WebRTC bookkeeping are undone");
  // second concurrent session (limiter already owned by the first): nothing to release
  frame_limiter_sessions = running_sessions = 2; limiter_stops = 0;
  check(run(true, false, false, true) == 1 && frame_limiter_sessions == 1 && running_sessions == 1 && limiter_stops == 0,
        "a throwing second session leaves the first session's limiter alone");
  // an input-only session never counted toward the limiter
  frame_limiter_sessions = 0; running_sessions = 1; limiter_stops = 0;
  check(run(false, true, false, true) == 1 && frame_limiter_sessions == 0 && limiter_stops == 0,
        "an uncounted session does not touch the limiter");
  // a successful start disables the guard
  frame_limiter_sessions = running_sessions = 1; limiter_stops = 0;
  check(run(true, true, true, false) == 0 && frame_limiter_sessions == 1 && running_sessions == 1 && limiter_stops == 0,
        "a successful start keeps every counter and the limiter");
  std::cout << (failures ? "FAIL" : "PASS") << " start guard\n";
  return failures != 0;
}
"""
code = code.replace('GUARD', guard)
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code, encoding='utf-8')
    subprocess.run(['g++', '-std=c++20', '-g', '-pthread', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I' + str(ROOT), '-I' + os.environ.get('AUDIT_JSON_INCLUDE', '/usr/include'), str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    result = subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'), timeout=30)
    raise SystemExit(result.returncode != 0)
