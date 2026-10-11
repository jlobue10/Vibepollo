#!/usr/bin/env python3
"""Compile src/login_backoff.h and check the per-address login failure backoff.

/api/auth/login used to answer every guess at the io thread's rate with no counter, delay or lockout.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TEST = r'''
#include "login_backoff.h"
#include <iostream>
#include <string>
using confighttp::login_backoff_t;
using namespace std::chrono_literals;
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  login_backoff_t backoff(5, 10min, 15min, 8);
  const auto t0 = login_backoff_t::clock::time_point {} + 24h;
  bool free_ok = true;
  for (int i = 0; i < 5; ++i) {
    free_ok = free_ok && backoff.check("a", t0).allowed && backoff.note_failure("a", t0) == 0s;
  }
  check(free_ok, "the first five failures cost nothing");
  check(backoff.note_failure("a", t0) == 2s, "the sixth failure locks the address out for two seconds");
  const auto locked = backoff.check("a", t0 + 1s);
  check(!locked.allowed && locked.retry_after >= 1s, "a locked address is refused with a retry hint");
  check(backoff.check("b", t0 + 1s).allowed, "other addresses are unaffected");
  check(backoff.check("a", t0 + 2s).allowed, "the lockout expires");
  check(backoff.note_failure("a", t0 + 2s) == 4s, "the next failure doubles the lockout");
  backoff.note_success("a");
  check(backoff.check("a", t0 + 3s).allowed && backoff.size() == 0, "a successful login clears the address");
  for (int i = 0; i < 5; ++i) backoff.note_failure("w", t0);
  check(backoff.note_failure("w", t0 + 11min) == 0s, "failures older than the window do not count");
  std::chrono::seconds last = 0s;
  for (int i = 0; i < 40; ++i) last = backoff.note_failure("c", t0 + std::chrono::hours(1));
  check(last == 15min, "the lockout is capped");
  for (int i = 0; i < 20; ++i) backoff.note_failure("addr-" + std::to_string(i), t0 + 2h);
  check(backoff.size() <= 8, "the table is bounded");
  return failures ? 1 : 0;
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='login-backoff-') as temp:
        cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
        cpp.write_text(TEST, encoding='utf-8')
        # Copy the self-contained header next to the test instead of -I src: on a Windows
        # toolchain src/process.h would shadow the CRT <process.h> that <chrono> pulls in.
        shutil.copy(ROOT / 'src/login_backoff.h', Path(temp) / 'login_backoff.h')
        try:
            subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-I', temp,
                            str(cpp), '-o', str(binary)], check=True)
        except subprocess.CalledProcessError:
            print('FAIL: src/login_backoff.h does not compile (or is missing)')
            return 1
        return subprocess.run([str(binary)]).returncode


if __name__ == '__main__':
    sys.exit(main())
