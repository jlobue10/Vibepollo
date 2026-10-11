#!/usr/bin/env python3
"""Exercise production launch polling and worker admission with a controlled scheduler.

Only the OS thread/command boundary is fake: the real poll, captured worker ticket,
lifecycle-gate check, completion guard, and cancellation statement are compiled.
Use --baseline to reproduce against HEAD before the fix.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
    return (ROOT / path).read_text()


def block(text, marker):
    start = text.index(marker)
    end = text.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (text[end] == '{') - (text[end] == '}')
    return text[start:end + 1]


source = read('src/process.cpp')
header = read('src/process.h')
state = read('src/deferred_launch_claim.h')
running = block(source, 'int proc_t::running()')
running = running[:running.index('    if (_steam_tracking_active)')] + 'return normal_result;\n}'
running = running.replace('std::thread(', 'ScheduledThread(')
resume = block(source, 'void proc_t::resume_deferred_launch(')
resume = resume[:resume.index('    std::optional<int> rtss_warmup_limit;')] + '\n++launches; normal_result = _app_id;\n}'
active = block(source, 'bool proc_t::is_launch_deferred() const')
members = re.search(r'    (?:std::atomic<bool>|deferred_launch::state_t) _deferred_launch[\s\S]+?(?=    bool _lossless_should_start_support)', header).group()
cancel = re.search(r'_deferred_launch(?: = false|\.cancel\(\));', block(source, 'void proc_t::terminate(')).group()
defer = re.search(r'_deferred_launch(?: = true|\.defer\(\));', block(source, 'int proc_t::execute(')).group()

code = r'''
#include <atomic>
#include <functional>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include "state.h"
#define _WIN32 1
#define BOOST_LOG(level) Log{}
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
namespace util {
  template<class F> struct guard { F action; ~guard() { action(); } };
  template<class F> guard<F> fail_guard(F action) { return {action}; }
}
namespace nvhttp { std::mutex &stream_lifecycle_mutex() { static std::mutex m; return m; } }
using HANDLE = void*;
void CloseHandle(HANDLE) {}
namespace platf {
  bool user_ready = true;
  bool is_running_as_system() { return true; }
  namespace dxgi { HANDLE retrieve_users_token(bool) { return user_ready ? reinterpret_cast<HANDLE>(1) : nullptr; } }
}
std::vector<std::function<void()>> jobs;
bool fail_thread = false;
struct ScheduledThread {
  explicit ScheduledThread(std::function<void()> job) {
    if (fail_thread) throw std::runtime_error("thread unavailable");
    jobs.push_back(std::move(job));
  }
  void detach() {}
};
void run_next() {
  auto job = std::move(jobs.front()); jobs.erase(jobs.begin()); job();
}
namespace proc {
struct proc_t {
  std::atomic<int> _app_id {-1};
  struct { std::string name = "game"; } _app;
  int normal_result = 0, launches = 0;
  MEMBERS
  int running();
  bool is_launch_deferred() const;
  void defer(int id) { _app_id = id; normal_result = 0; DEFER }
  void cancel() { CANCEL _app_id = -1; normal_result = 0; }
};
RUNNING
RESUME
ACTIVE
}
int main() {
  int checks = 0, failures = 0;
  auto check = [&](bool ok, const char *what) { ++checks; failures += !ok; std::cout << (ok ? "PASS " : "FAIL ") << what << '\n'; };
  {
    proc::proc_t p;
    p.defer(42);
    platf::user_ready = false;
    check(p.running() == 42 && jobs.empty(), "before sign-in the launch remains deferred");
    platf::user_ready = true;
    // No commands run inline, even when the lifecycle gate is already held.
    std::unique_lock<std::mutex> gate(nvhttp::stream_lifecycle_mutex());
    check(p.running() == 42 && jobs.size() == 1 && p.launches == 0, "the poll schedules a worker without blocking on the lifecycle gate");
    check(p.running() == 42 && p.is_launch_deferred() && jobs.size() == 1,
          "later polls still report the app while its worker is queued or launching");
    gate.unlock(); run_next();
    check(p.launches == 1 && !p.is_launch_deferred(), "a completed launch releases its own request");
  }
  {
    proc::proc_t p; p.defer(42); p.running(); p.cancel(); run_next();
    check(p.launches == 0 && !p.is_launch_deferred(), "a cancelled worker cannot launch the app");
  }
  {
    proc::proc_t p; p.defer(42); p.running(); p.cancel();
    p._app_id = 42; p.normal_result = 42; // A new immediate launch reused the app id.
    run_next();
    check(p.launches == 0, "an old worker cannot relaunch a replacement with the same app id");
  }
  {
    proc::proc_t p; p.defer(42); p.running(); p.cancel(); p.defer(42); p.running();
    run_next();
    check(p.launches == 0 && p.is_launch_deferred(), "old completion leaves the replacement deferral intact");
    p.running();
    while (!jobs.empty()) run_next();
    check(p.launches == 1 && !p.is_launch_deferred(), "only the replacement worker starts the app");
  }
  {
    proc::proc_t p; p.defer(42); p.running();
    p._app_id = 84; // Catalog refresh changed the id of the same active app UUID.
    run_next();
    check(p.launches == 1, "catalog id changes do not cancel the same launch generation");
  }
  {
    proc::proc_t p; p.defer(42); fail_thread = true; p.running(); fail_thread = false;
    check(jobs.empty() && p.is_launch_deferred(), "thread creation failure retains the request");
    p.running(); run_next();
    check(p.launches == 1 && !p.is_launch_deferred(), "the next poll retries a failed thread creation once");
  }
  std::cout << checks << " checks; " << failures << " failures\n";
  return failures != 0;
}
'''
for marker, value in {'MEMBERS': members, 'DEFER': defer, 'CANCEL': cancel,
                      'RUNNING': running, 'RESUME': resume, 'ACTIVE': active}.items():
    code = code.replace(marker, value)
with tempfile.TemporaryDirectory(prefix='deferred-lifecycle-') as directory:
    work = Path(directory)
    (work / 'state.h').write_text(state)
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-g', '-pthread', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], timeout=10,
                                   env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
