#!/usr/bin/env python3
"""Compile src/csrf_store.h and check the bounded CSRF token store.

/api/csrf-token is unauthenticated; the store used to be an uncapped map keyed by the raw
Authorization header, so any client could grow it without bound.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TEST = r'''
#include "csrf_store.h"
#include <iostream>
#include <string>
using confighttp::csrf_store_t;
using namespace std::chrono_literals;
int main() {
  int failures = 0, counter = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  csrf_store_t store(4096, 1h, [&] { return std::to_string(++counter); });
  const auto t0 = csrf_store_t::clock::time_point {} + 24h;
  const std::string first = store.issue("10.0.0.1", t0);
  check(store.issue("10.0.0.1", t0 + 1s) == first, "a client keeps its token while it is valid");
  for (int i = 0; i < 10000; ++i) store.issue("junk-" + std::to_string(i), t0 + std::chrono::milliseconds(i + 1));
  check(store.size() <= 4096, "10000 distinct ids leave at most the capacity");
  const std::string recent = store.issue("10.0.0.2", t0 + 11s);
  check(store.validate("10.0.0.2", recent, t0 + 12s) == csrf_store_t::validation_e::ok, "a recent id survives eviction");
  check(store.validate("10.0.0.1", first, t0 + 12s) == csrf_store_t::validation_e::missing, "the oldest id was evicted first");
  check(store.validate("10.0.0.2", "nope", t0 + 13s) == csrf_store_t::validation_e::mismatch, "a wrong token is rejected");
  const std::string stale = store.issue("10.0.0.3", t0 + 20s);
  check(store.validate("10.0.0.3", stale, t0 + 20s + 2h) == csrf_store_t::validation_e::expired, "an expired token is rejected");
  check(store.issue("10.0.0.3", t0 + 20s + 2h) != stale, "an expired client gets a fresh token");
  store.issue("10.0.0.4", t0 + 30s);
  store.validate("10.0.0.4", "x", t0 + 30s + 2h);
  check(store.issue("10.0.0.5", t0 + 30s + 3h) != "" && store.size() <= 4096, "expired entries are pruned on issue");
  store.clear();
  check(store.size() == 0, "clear empties the store");
  return failures ? 1 : 0;
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='csrf-store-') as temp:
        cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
        cpp.write_text(TEST, encoding='utf-8')
        # Copy the self-contained header next to the test instead of -I src: on a Windows
        # toolchain src/process.h would shadow the CRT <process.h> that <chrono> pulls in.
        shutil.copy(ROOT / 'src/csrf_store.h', Path(temp) / 'csrf_store.h')
        try:
            subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-I', temp,
                            str(cpp), '-o', str(binary)], check=True)
        except subprocess.CalledProcessError:
            print('FAIL: src/csrf_store.h does not compile (or is missing)')
            return 1
        return subprocess.run([str(binary)]).returncode


if __name__ == '__main__':
    sys.exit(main())
