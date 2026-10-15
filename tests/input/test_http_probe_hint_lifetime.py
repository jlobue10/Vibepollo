#!/usr/bin/env python3
"""Run HTTP capability discovery and its idle probe with deterministic thread interleavings.

The production NVHTTP functions and video hint lease setters/clearers run unchanged.
GPU enumeration and probing are boundary fakes; this is not a Windows build or a GPU test.
--baseline reads production code from HEAD while retaining these regression scenarios.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
    return (ROOT / path).read_text()


def block(source, marker):
    start = source.index(marker)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


source = read('src/nvhttp.cpp')
video = read('src/video.cpp')
functions = '\n'.join(block(source, marker) for marker in [
    '    idle_virtual_adapter_hint_t publish_idle_virtual_adapter_hint()',
    '    void clear_idle_virtual_adapter_hint(',
    '    void run_idle_http_capability_probe()',
    '    http_encoder_capabilities_t advertised_encoder_capabilities_for_http()',
])
# Keep the real bounded-wait policy, with a fake clock at the timing boundary.
functions = functions.replace('std::chrono::steady_clock::now()', 'clock_now()')
functions = functions.replace('std::this_thread::sleep_for(kHttpCapabilityProbePoll)', 'clock_sleep(kHttpCapabilityProbePoll)')
hint_type = block(source, '    struct idle_virtual_adapter_hint_t') + ';'
leases = '\n'.join(block(video, marker) for marker in [
    '  encoder_probe_adapter_hint_lease_t set_pending_virtual_display_adapter_hint(',
    '  bool clear_pending_virtual_display_adapter_hint(',
])

code = r'''
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <iostream>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include "http_capability_probe_policy.h"
using namespace std::chrono_literals;
std::atomic<std::int64_t> clock_ms{0};
auto clock_now() { return std::chrono::steady_clock::time_point{} + std::chrono::milliseconds(clock_ms.load()); }
void clock_sleep(std::chrono::milliseconds delay) { clock_ms.fetch_add(delay.count()); std::this_thread::yield(); }
struct silent_log { template<class T> silent_log& operator<<(const T&) { return *this; } };
#define BOOST_LOG(level) silent_log{}
namespace util {
template<class F> struct guard { F fn; ~guard() { fn(); } };
template<class F> auto fail_guard(F fn) { return guard<F>{std::move(fn)}; }
}
// Rendezvous points sit at OS/GPU boundaries, never inside rewritten production code.
struct signal_t {
  std::mutex mutex; std::condition_variable cv; bool ready=false;
  void set() { std::lock_guard lock(mutex); ready=true; cv.notify_all(); }
  void wait() {
    std::unique_lock lock(mutex);
    if (!cv.wait_for(lock, 3s, [&] { return ready; })) throw std::runtime_error("thread rendezvous timed out");
  }
};
using LUID = int;
std::string luid_cache_identity(LUID id) { return std::to_string(id); }
namespace config {
struct video_t { enum class virtual_display_mode_e { disabled, enabled };
  virtual_display_mode_e virtual_display_mode=virtual_display_mode_e::enabled;
  std::string adapter_name, adapter_pnp_id;
} video;
}
std::atomic_bool active{false};
bool has_stream_session_activity() { return active.load(); }
bool has_active_or_stopping_stream_session() { return active.load(); }
std::mutex lifecycle;
std::mutex& stream_lifecycle_mutex() { return lifecycle; }
thread_local bool slow_resolver=false;
signal_t *resolver_entered=nullptr, *release_resolver=nullptr;
namespace platf {
struct adapter_t { std::optional<LUID> luid=7; explicit operator bool() const { return luid.has_value(); } };
adapter_t resolve_preferred_render_adapter(const std::string&, const std::string&) {
  if (slow_resolver) { resolver_entered->set(); release_resolver->wait(); }
  return {};
}
}
namespace video {
using encoder_probe_adapter_hint_lease_t=std::uint64_t;
struct cache_t {
  std::mutex mutex;
  std::optional<LUID> pending_virtual_display_adapter_hint;
  std::uint64_t pending_virtual_display_adapter_hint_lease=0;
  std::uint64_t next_pending_virtual_display_adapter_hint_lease=0;
  bool pending_virtual_display_adapter_hint_ready_for_verification=false;
} cache;
cache_t& encoder_probe_cache_state() { return cache; }
@LEASES@
std::uint64_t current_lease() { std::lock_guard lock(cache.mutex); return cache.pending_virtual_display_adapter_hint_lease; }
struct advertised_encoder_capabilities_t { int hevc_mode=0, av1_mode=0, pyrowave_mode=0; };
std::atomic_uint probes{0};
std::atomic_bool cache_ready{false};
std::uint64_t expected_probe_lease=0;
bool probe_kept_hint=true;
advertised_encoder_capabilities_t advertised_encoder_capabilities(bool probe, bool* complete) {
  if (probe) {
    ++probes; probe_kept_hint=current_lease()==expected_probe_lease && expected_probe_lease!=0;
    cache_ready=probe_kept_hint;
  }
  // Only a hint for the expected adapter can reuse its cached positive result.
  std::lock_guard lock(cache.mutex);
  *complete=cache_ready && cache.pending_virtual_display_adapter_hint==7;
  return {};
}
}
signal_t *probe_reached=nullptr, *release_probe=nullptr;
std::atomic_bool probe_entered{false};
namespace VDISPLAY {
struct result_t { bool owns_temporary_probe_request() const { return true; } bool ready_for_capture() const { return true; } };
result_t ensure_display(std::optional<LUID>) {
  video::expected_probe_lease=video::current_lease();
  probe_entered=true;
  if (probe_reached) probe_reached->set();
  if (release_probe) release_probe->wait();
  return {};
}
void cleanup_ensure_display(result_t&) {}
}
struct http_encoder_capabilities_t { video::advertised_encoder_capabilities_t advertised; bool probe_complete; };
// The executor is stubbed: each scenario explicitly dispatches the real worker function.
enum class idle_probe_schedule_e { started, already_running, unavailable };
bool scheduled_with_gate_held=false;
idle_probe_schedule_e schedule_result=idle_probe_schedule_e::unavailable;
std::function<void()> on_schedule;
idle_probe_schedule_e schedule_idle_http_capability_probe() {
  // Check from a different thread: try_lock by a mutex's owner is undefined.
  std::thread observer([] { if (lifecycle.try_lock()) lifecycle.unlock(); else scheduled_with_gate_held=true; });
  observer.join();
  if (on_schedule) on_schedule();
  return schedule_result;
}
// Baseline code uses this observation flag; fixed code uses the lifecycle gate.
std::atomic_bool http_capability_probe_running{false};
std::atomic<unsigned> http_capability_probe_finished{0};
constexpr auto kHttpCapabilityProbePoll=50ms;
@HINT_TYPE@
@FUNCTIONS@
int main() {
  int checks=0, failures=0;
  auto check=[&](bool ok, const char* name) { ++checks; failures+=!ok; std::cout<<(ok?"PASS ":"FAIL ")<<name<<'\n'; };

  // Pause discovery after its idle/running checks but before publishing its hint.
  // An idle worker starting in this window must not publish a competing lease.
  signal_t entered, resume, worker_progress, finish_probe;
  resolver_entered=&entered; release_resolver=&resume;
  probe_reached=&worker_progress; release_probe=&finish_probe;
  std::thread discovery([] { slow_resolver=true; advertised_encoder_capabilities_for_http(); });
  entered.wait();
  std::thread worker([&] { run_idle_http_capability_probe(); worker_progress.set(); });
  worker_progress.wait();
  const bool overlapping=probe_entered.load();
  const auto worker_lease=video::current_lease();
  resume.set(); discovery.join();
  check(!overlapping || video::current_lease()==worker_lease,
        "discovery cannot replace and clear an idle worker's adapter hint");
  finish_probe.set(); worker.join();
  check(!overlapping || video::probe_kept_hint,
        "the encoder probe retains its expected adapter through capability sampling");
  check(video::current_lease()==0, "idle hint ownership is released after both operations finish");
  // No other gate owner remains: scheduling must happen outside discovery's own lease.
  scheduled_with_gate_held=false;
  advertised_encoder_capabilities_for_http();
  check(!scheduled_with_gate_held, "discovery releases the lifecycle gate before scheduling the worker");

  // The inverse ordering must keep discovery responsive while the GPU stalls.
  probe_entered=false; signal_t reached2, finish2;
  probe_reached=&reached2; release_probe=&finish2;
  schedule_result=idle_probe_schedule_e::already_running;
  std::thread worker2([] { run_idle_http_capability_probe(); });
  reached2.wait(); const auto running_lease=video::current_lease();
  signal_t answered;
  std::thread discovery2([&] { advertised_encoder_capabilities_for_http(); answered.set(); });
  answered.wait(); discovery2.join();
  check(video::current_lease()==running_lease && running_lease!=0,
        "discovery answers during a stalled GPU probe without mutating its hint");
  finish2.set(); worker2.join();
  check(video::probe_kept_hint, "the running probe keeps its adapter after concurrent discovery");
  check(video::current_lease()==0, "the running probe releases its hint on completion");

  probe_reached=nullptr; release_probe=nullptr;
  schedule_result=idle_probe_schedule_e::unavailable;
  video::cache_ready=false;
  const auto probes_before=video::probes.load();
  {
    std::unique_lock lock(lifecycle);
    auto owner=video::set_pending_virtual_display_adapter_hint(9);
    std::thread rejected([] { run_idle_http_capability_probe(); }); rejected.join();
    check(video::current_lease()==owner, "a worker refused the lifecycle gate cannot overwrite its owner's hint");
    video::clear_pending_virtual_display_adapter_hint(owner);
  }
  check(video::probes==probes_before, "a worker refused the lifecycle gate performs no GPU probe");
  run_idle_http_capability_probe();
  check(video::probes==probes_before+1 && video::probe_kept_hint && video::current_lease()==0,
        "a later idle worker can still probe and clean up normally");

  // Finish the real worker between scheduling and the first wait-loop sample.
  // Its hint is gone by then, but the intended adapter's cache must still match.
  video::cache_ready=false; schedule_result=idle_probe_schedule_e::started;
  scheduled_with_gate_held=false;
  on_schedule=[] { std::thread completed([] { run_idle_http_capability_probe(); }); completed.join(); };
  auto answer=advertised_encoder_capabilities_for_http();
  check(answer.probe_complete, "a probe completed before the next poll returns the cached codec capabilities");
  check(!scheduled_with_gate_held && video::current_lease()==0,
        "waiting discovery releases its hint before scheduling and cleans up every cache sample");
  std::cout<<checks<<" checks, "<<failures<<" failures\n";
  return failures!=0;
}
'''
code = code.replace('@LEASES@', leases).replace('@HINT_TYPE@', hint_type).replace('@FUNCTIONS@', functions)
with tempfile.TemporaryDirectory(prefix='http-probe-hint-') as directory:
    work = Path(directory)
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-g', '-Wall', '-Wextra', '-I' + str(ROOT / 'src'), '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', str(work / 'test.cpp'), '-pthread', '-o', str(work / 'test')], check=True)
    result = subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
    raise SystemExit(result.returncode)
