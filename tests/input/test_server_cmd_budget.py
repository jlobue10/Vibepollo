#!/usr/bin/env python3
"""Compile src/server_cmd_budget.h and check the per-session token bucket for IDX_EXEC_SERVER_CMD.

A client with the server_cmd permission used to start one thread and one process per control
message with no bound; the bucket admits a short burst, then one command per second.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TEST = r'''
#include "server_cmd_budget.h"
#include <iostream>
using stream::server_cmd_budget_t;
using namespace std::chrono_literals;
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  server_cmd_budget_t budget;
  const auto t0 = server_cmd_budget_t::clock::time_point {} + 24h;
  int admitted = 0;
  for (int i = 0; i < 100; ++i) admitted += budget.admit(t0);
  check(admitted == 4, "a burst of 100 messages runs at most four commands");
  check(budget.rejected() == 96, "the rejected count is kept for the log");
  check(!budget.admit(t0 + 500ms), "half a second later nothing has refilled");
  check(budget.admit(t0 + 1s) && !budget.admit(t0 + 1s), "one command per second is refilled");
  check(budget.admit(t0 + 3s) && budget.admit(t0 + 3s) && !budget.admit(t0 + 3s), "refill accumulates while idle");
  int later = 0;
  for (int i = 0; i < 10; ++i) later += budget.admit(t0 + 60s);
  check(later == 4, "the bucket never exceeds its capacity after a long idle");
  return failures ? 1 : 0;
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='server-cmd-budget-') as temp:
        cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
        cpp.write_text(TEST, encoding='utf-8')
        # Copied next to the test rather than -I src: a Windows toolchain would resolve <process.h> to src/process.h.
        shutil.copy(ROOT / 'src/server_cmd_budget.h', Path(temp) / 'server_cmd_budget.h')
        try:
            subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-I', temp, str(cpp), '-o', str(binary)], check=True)
        except subprocess.CalledProcessError:
            print('FAIL: src/server_cmd_budget.h does not compile (or is missing)')
            return 1
        return subprocess.run([str(binary)]).returncode


if __name__ == '__main__':
    sys.exit(main())
