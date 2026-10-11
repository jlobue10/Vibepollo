#!/usr/bin/env python3
"""Race the deferred-launch claim (src/deferred_launch_claim.h) from many threads.

Exactly one poller may resume a deferred app launch; the rest see it already claimed,
a released claim can be taken again, and a requeued one is offered to the next poll.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
code = r"""
#include <atomic>
#include <cassert>
#include <iostream>
#include <thread>
#include <vector>
#include "src/deferred_launch_claim.h"
using namespace proc::deferred_launch;
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *what) { std::cout << (ok ? "PASS " : "FAIL ") << what << '\n'; failures += !ok; };
  std::atomic<bool> deferred {false}, in_flight {false};
  check(!claim(deferred, in_flight) && !in_flight.load(), "nothing to claim when no launch is deferred");
  for (int round = 0; round < 500; ++round) {
    deferred.store(true);
    std::atomic<int> winners {0};
    std::atomic<bool> go {false};
    std::vector<std::thread> pollers;
    for (int t = 0; t < 8; ++t) {
      pollers.emplace_back([&] {
        while (!go.load()) {}
        if (claim(deferred, in_flight)) ++winners;
      });
    }
    go.store(true);
    for (auto &p : pollers) p.join();
    if (winners.load() != 1 || deferred.load() || !in_flight.load()) {
      check(false, "exactly one of eight racing pollers claims the launch");
      return 1;
    }
    if (claim(deferred, in_flight)) { check(false, "a second deferral cannot be claimed while one is in flight"); return 1; }
    release(in_flight);
  }
  check(true, "exactly one of eight racing pollers claims the launch (500 rounds)");
  deferred.store(true);
  check(claim(deferred, in_flight), "after release the next deferral is claimable");
  requeue(deferred, in_flight);
  check(deferred.load() && !in_flight.load() && claim(deferred, in_flight), "a requeued launch is offered again");
  release(in_flight);
  std::cout << (failures ? "FAIL" : "PASS") << " deferred launch claim\n";
  return failures != 0;
}
"""
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code, encoding='utf-8')
    subprocess.run(['g++', '-std=c++20', '-g', '-pthread', '-fsanitize=thread',
                    '-I' + str(ROOT), str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    result = subprocess.run([str(work / 'test')], env=dict(os.environ), timeout=120)
    raise SystemExit(result.returncode != 0)
