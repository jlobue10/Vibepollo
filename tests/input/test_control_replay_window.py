#!/usr/bin/env python3
"""Exercise the production encrypted-control replay gate after authentication.

ENet orders each channel independently. The client's encryption sequence is
global, so reliable releases can arrive behind newer mouse/sensor messages.
--baseline extracts the gate and state from HEAD; no sockets or keys are used.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/stream.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/stream.cpp').read_text())


def block(text, marker, start=0):
    first = text.index(marker, start)
    end = text.index('{', first)
    depth = 1
    while depth:
        end += 1
        depth += (text[end] == '{') - (text[end] == '}')
    return text[first:end + 1]


gate = block(source, 'if (session->config.encryptionFlagsEnabled & SS_ENC_CONTROL_V2)',
             source.index('if (cipher.decrypt(*tagged_cipher, plaintext, &iv))',
                          source.index('server->map(packetTypes[IDX_ENCRYPTED]')))
state_start = source.index('      // Highest client sequence') if 'incoming_seq_high_water' in source else source.index('      control_replay_window incoming_replay;')
state_end = source.index('      std::uint32_t connect_data;', state_start)
state = source[state_start:state_end]

prefix = r'''
#include <cstdint>
#include <iostream>
#include <limits>
#include <optional>
#include <vector>
#include "control_replay_window.h"
using stream::control_replay_window;
constexpr unsigned SS_ENC_CONTROL_V2 = 1;
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) Log{}
struct Session {
  struct { unsigned encryptionFlagsEnabled = SS_ENC_CONTROL_V2; } config;
  struct {
STATE
  } control;
  std::vector<std::uint32_t> dispatched;
};
void receive_authenticated(Session *session, std::uint32_t sequence) {
  std::optional<std::uint32_t> seq = sequence;
GATE
  session->dispatched.push_back(sequence);
}
bool receive(Session &s, std::uint32_t n) {
  auto before = s.dispatched.size();
  receive_authenticated(&s, n);
  return s.dispatched.size() != before;
}
'''
suffix = r'''
int main() {
  int checks = 0, failures = 0;
  auto check = [&](bool ok, const char *name) {
    ++checks; failures += !ok; std::cout << (ok ? "PASS " : "FAIL ") << name << '\n';
  };
  Session session;
  check(receive(session, 0), "first sequence zero is valid");
  check(!receive(session, 0), "the first packet cannot be replayed");
  check(receive(session, 100), "a newer mouse/sensor message is delivered");
  check(receive(session, 98), "a delayed reliable keyboard release on another channel is delivered");
  check(receive(session, 99), "a delayed controller release on another channel is delivered");
  check(!receive(session, 98), "replaying a reordered release is rejected");
  check(!receive(session, 100), "replaying the newest message is rejected");
  check(receive(session, 101), "normal increasing traffic still works");
  check(!receive(session, 99), "window advancement retains older replay marks");
  Session burst;
  check(receive(burst, 8000), "a burst on an independent channel may arrive first");
  bool all = true;
  for (std::uint32_t i = 7999; i >= 1; --i) all &= receive(burst, i);
  check(all, "a reordered burst is delivered exactly once throughout the window");
  bool repeats = false;
  for (std::uint32_t i = 1; i <= 8000; ++i) repeats |= receive(burst, i);
  check(!repeats, "every duplicate in the reordered burst is rejected");
  Session edge;
  constexpr std::uint32_t window = 65536;
  check(receive(edge, window), "window can start above zero");
  check(receive(edge, 1), "oldest unseen packet inside the window is delivered");
  check(!receive(edge, 0), "packets outside the retained window are rejected");
  check(receive(edge, window + 1) && !receive(edge, 1), "advancing the window expires its oldest sequence");
  check(receive(edge, 2 * window + 2), "a jump beyond the entire window is accepted");
  check(receive(edge, 2 * window + 1), "a large jump clears obsolete marks");
  check(!receive(edge, window + 1), "a large jump does not permit ancient replays");
  Session end;
  const auto last = std::numeric_limits<std::uint32_t>::max();
  check(receive(end, last), "the final 32-bit sequence is valid");
  check(receive(end, last - 1), "reordering works at the 32-bit boundary");
  check(!receive(end, 0), "sequence wrap cannot reuse a GCM nonce");
  Session other;
  check(receive(other, 98), "sessions have independent replay state");
  Session legacy;
  legacy.config.encryptionFlagsEnabled = 0;
  check(receive(legacy, 100) && receive(legacy, 99) && receive(legacy, 100),
        "the v2 replay gate does not alter legacy control encryption");
  std::cout << checks << " checks, " << failures << " failures\n";
  return failures ? 1 : 0;
}
'''
with tempfile.TemporaryDirectory(prefix='control-replay-') as directory:
    work = Path(directory)
    (work / 'test.cpp').write_text(prefix.replace('STATE', state).replace('GATE', gate) + suffix)
    # The old source has no helper header. It is only included to compile the fixed state.
    if 'incoming_seq_high_water' in source:
        (work / 'control_replay_window.h').write_text('namespace stream { class control_replay_window {}; }\n')
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I' + str(ROOT / 'src'), str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
