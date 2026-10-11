#!/usr/bin/env python3
"""Compile the production Remote Monitor coordinator (src/remote_display_topology.cpp) with fake
runtime callbacks and check that a lease whose display was lost is recreated on the next
activation and that the watchdog-failure path (forget_all_leases) leads to recreation too.

Before the fix a lost display left every activate/resume retryable forever (one creation, never
another). No platform display code runs. --baseline reads HEAD.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
    return (ROOT / path).read_text(encoding='utf-8')


MAIN = r"""
#include "remote_display_topology.h"
#include <iostream>
int main() {
  using remote_display_topology::coordinator_t; using remote_display_topology::runtime_callbacks_t; using remote_display_topology::node_t;
  using monitor_mode_t = remote_display_topology::mode_t;  // sys/types.h also has a mode_t
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  int creations = 0;
  bool display_present = true;
  coordinator_t c;
  runtime_callbacks_t callbacks;
  callbacks.create_or_reclaim = [&](const std::string &, const std::string &, const monitor_mode_t &) { ++creations; display_present = true; return true; };
  callbacks.apply_composed_topology = [](const std::vector<node_t> &) { return true; };
  callbacks.exact_target_has_current_mode_and_dxgi = [&](const std::string &, const monitor_mode_t &) -> std::optional<std::string> {
    if (!display_present) return std::nullopt;
    return std::string("\\\\.\\DISPLAY9");
  };
  callbacks.remove_owned_display = [&](const std::string &) { display_present = false; return true; };
  callbacks.client_identity_capacity = [] { return std::size_t {4}; };
#ifdef HAS_LEASE_PRESENT
  callbacks.lease_present = [&](const std::string &) { return display_present; };
#endif
  c.set_runtime_callbacks(std::move(callbacks));

  const auto first = c.activate_or_resume("client-a", "A", monitor_mode_t {}, 1);
  check(first.accepted && first.ready && creations == 1, "a first activation creates the display and is ready");

  // The driver dropped the display (watchdog failure, reinstall) without the coordinator hearing of it.
  display_present = false;
  const auto resumed = c.resume_remote_monitor("client-a");
  check(resumed.accepted && resumed.capture_ready && creations == 2, "resume recreates a display that was lost under a held lease");

  display_present = false;
  const auto again = c.activate_or_resume("client-a", "A", monitor_mode_t {}, 2);
  check(again.accepted && again.ready && creations == 3, "a new generation recreates a lost display as well");

  // A display that is still there is not recreated.
  const auto steady = c.resume_remote_monitor("client-a");
  check(steady.accepted && steady.capture_ready && creations == 3, "a present display is kept, not recreated");

  check(c.explicit_release("client-a", 2, "done"), "an explicit release of a present display succeeds");

  // The watchdog-failure path: every lease is forgotten, Resume recreates.
  const auto second = c.activate_or_resume("client-b", "B", monitor_mode_t {}, 1);
  check(second.ready && creations == 4, "another client activates");
  c.forget_all_leases();
  display_present = false;
  const auto after_forget = c.resume_remote_monitor("client-b");
  check(after_forget.accepted && after_forget.capture_ready && creations == 5, "after forget_all_leases a resume recreates the display");
  return failures ? 1 : 0;
}
"""


def main():
    header = read('src/remote_display_topology.h')
    source = read('src/remote_display_topology.cpp')
    capacity = read('src/virtual_display_capacity.h')
    with tempfile.TemporaryDirectory(prefix='remote-monitor-lease-') as temp:
        work = Path(temp)
        (work / 'remote_display_topology.h').write_text(header, encoding='utf-8')
        (work / 'virtual_display_capacity.h').write_text(capacity, encoding='utf-8')
        (work / 'remote_display_topology.cpp').write_text(source, encoding='utf-8')
        (work / 'test.cpp').write_text(MAIN, encoding='utf-8')
        defines = ['-DHAS_LEASE_PRESENT'] if 'lease_present' in header else []
        subprocess.run(['c++', '-std=c++20', '-Wall', '-Wno-unused-parameter', '-fsanitize=address,undefined', *defines, '-I', str(work),
                        str(work / 'remote_display_topology.cpp'), str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
        return subprocess.run([str(work / 'test')]).returncode


if __name__ == '__main__':
    sys.exit(main())
