#!/usr/bin/env python3
"""Race the production claim and deterministically pause a poller across completion.

The atomic wrapper only pauses after a real load; all loads, stores, and CAS
operations still use std::atomic. --baseline exercises the pre-fix API from HEAD.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
header = (subprocess.check_output(['git', 'show', 'HEAD:src/deferred_launch_claim.h'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/deferred_launch_claim.h').read_text())
legacy = 'class state_t' not in header
code = r"""
#include <atomic>
#include <cassert>
#include <iostream>
#include <optional>
#include <thread>
#include <vector>
thread_local bool pause_load = false;
std::atomic<bool> loaded {false}, resume_load {false};
template<class T> class HookedAtomic {
  std::atomic<T> value;
public:
  explicit HookedAtomic(T initial) : value(initial) {}
  T load(std::memory_order order = std::memory_order_seq_cst) const {
    T result = value.load(order);
    if (pause_load) {
      pause_load = false;
      loaded.store(true);
      while (!resume_load.load()) std::this_thread::yield();
    }
    return result;
  }
  void store(T next, std::memory_order order = std::memory_order_seq_cst) { value.store(next, order); }
  bool compare_exchange_strong(T &expected, T next, std::memory_order order = std::memory_order_seq_cst) {
    return value.compare_exchange_strong(expected, next, order);
  }
  bool compare_exchange_weak(T &expected, T next, std::memory_order order = std::memory_order_seq_cst) {
    return value.compare_exchange_weak(expected, next, order);
  }
};
#include "state.h"
#ifdef LEGACY
// Adapt calls, not the algorithm: the baseline functions above remain unchanged.
struct TestState {
  HookedAtomic<bool> deferred {false}, in_flight {false};
  void defer() { deferred.store(true); }
  void cancel() { deferred.store(false); }
  bool active() const { return deferred.load() || in_flight.load(); }
  std::optional<int> claim() {
    return proc::deferred_launch::claim(deferred, in_flight) ? std::optional<int>{1} : std::nullopt;
  }
  void release(int) { proc::deferred_launch::release(in_flight); }
  void requeue(int) { proc::deferred_launch::requeue(deferred, in_flight); }
};
#else
using TestState = proc::deferred_launch::state_t;
#endif
int main() {
  int failures = 0, checks = 0;
  auto check = [&](bool ok, const char *what) { ++checks; std::cout << (ok ? "PASS " : "FAIL ") << what << '\n'; failures += !ok; };
  TestState state;
  check(!state.claim() && !state.active(), "nothing to claim when no launch is deferred");
  bool race_ok = true;
  for (int round = 0; round < 500; ++round) {
    state.defer();
    std::atomic<int> winners {0};
    std::atomic<bool> go {false};
    std::vector<std::thread> pollers;
    for (int t = 0; t < 8; ++t) {
      pollers.emplace_back([&] {
        while (!go.load()) {}
        if (auto ticket = state.claim()) {
          ++winners;
          // Completion may happen before the remaining pollers finish claiming.
          state.release(*ticket);
        }
      });
    }
    go.store(true);
    for (auto &p : pollers) p.join();
    race_ok &= winners.load() == 1 && !state.active();
  }
  check(race_ok, "exactly one of eight racing pollers claims the launch (500 rounds, immediate completion)");
  state.defer();
  auto ticket = state.claim();
  check(ticket.has_value() && state.active() && !state.claim(), "a worker retains ownership while launching");
  state.requeue(*ticket);
  ticket = state.claim();
  check(ticket.has_value(), "a failed thread creation can be retried");
  state.release(*ticket);

  // Force a second poller's initial pending read to span a complete launch.
  state.defer();
  bool stale_claimed = false;
  std::thread delayed([&] {
    pause_load = true;
    if (auto stale = state.claim()) {
      stale_claimed = true;
      state.release(*stale);
    }
  });
  while (!loaded.load()) std::this_thread::yield();
  ticket = state.claim();
  assert(ticket);
  state.release(*ticket);
  resume_load.store(true);
  delayed.join();
  check(!stale_claimed && !state.active(), "a stale pending read cannot claim a completed launch again");

  state.defer(); ticket = state.claim(); state.cancel(); state.requeue(*ticket);
  check(!state.active() && !state.claim(), "a thread creation failure cannot revive a cancelled launch");

  state.cancel(); state.defer(); ticket = state.claim(); state.cancel(); state.defer();
  auto replacement = state.claim();
  state.release(*ticket);
  check(replacement.has_value() && state.active() && !state.claim(), "old completion cannot release the replacement worker");
  if (replacement) state.release(*replacement);
  std::cout << checks << " checks; " << failures << " failures\n";
  return failures != 0;
}
"""
with tempfile.TemporaryDirectory(prefix='deferred-claim-') as temp:
    work = Path(temp)
    (work / 'state.h').write_text(header.replace('std::atomic<', 'HookedAtomic<'))
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-g', '-pthread', '-fsanitize=thread',
                    *(['-DLEGACY'] if legacy else []),
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    result = subprocess.run([str(work / 'test')], env=dict(os.environ), timeout=120)
    raise SystemExit(result.returncode != 0)
