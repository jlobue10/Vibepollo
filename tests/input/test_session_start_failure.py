#!/usr/bin/env python3
"""Fault-inject session startup with the real registration/destruction code.

The shared control thread is stepped deterministically: a failed session must
remain alive until its controlEnd acknowledgement, and its raw registry/peer
entries must be gone before its fields are destroyed. Socket/GPU work is omitted;
the production STOPPING branch, destructor, synchronization and packet fence run.
--baseline reproduces the freed-session read in the source at HEAD under ASan.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
baseline = '--baseline' in sys.argv
source = (subprocess.check_output(['git', 'show', 'HEAD:src/stream.cpp'], cwd=ROOT, text=True)
          if baseline else (ROOT / 'src/stream.cpp').read_text())

def block(text, marker):
    start = text.index(marker)
    masked = re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"',
                    lambda m: ' ' * len(m.group()), text, flags=re.S)
    end = masked.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (masked[end] == '{') - (masked[end] == '}')
    return text[start:end + 1]

start = block(source, 'int start(session_t &session,')
publish_at = start.index('auto lg = session.broadcast_ref->control_server._sessions.lock();')
publication = start[publish_at:start.index('\n      }', publish_at)]
if not baseline:
    assert start.index('session.pingTimeout =') < publish_at
    assert start.index('session.video.peer.address(addr);') < publish_at
    assert start.index('session.audio.peer.address(addr);') < publish_at
retirement = (block(source, 'void control_server_t::retire_session(')
              if 'void control_server_t::retire_session(' in source else '')
stopping = block(source, 'if (session->state.load(std::memory_order_acquire) == session::state_e::STOPPING)')
code = r'''
#include <algorithm>
#include <atomic>
#include <cassert>
#include <chrono>
#include <future>
#include <iostream>
#include <memory>
#include <thread>
#include <unordered_map>
#include <vector>
#include "src/thread_safe.h"
#include "src/sync.h"
#include "src/stream_packet.h"
using namespace std::literals;
namespace mail {constexpr std::string_view shutdown = "shutdown";}
namespace stream {
namespace session {enum class state_e {STOPPED, STOPPING, STARTING, RUNNING};}
struct session_t;
struct control_server_t {
  sync_util::sync_t<std::vector<session_t*>> _sessions;
  sync_util::sync_t<std::unordered_map<void*, session_t*>> _peer_to_session;
  void retire_session(session_t& session);
};
struct broadcast_t {control_server_t control_server;};
struct session_t {
  packet_channel_t packet_channel = std::make_shared<packet_channel_state_t>(this);
  safe::mail_t mail = std::make_shared<safe::mail_raw_t>();
  safe::mail_raw_t::event_t<bool> shutdown_event = mail->event<bool>(::mail::shutdown);
  safe::signal_t controlEnd;
  std::atomic<session::state_e> state {session::state_e::STOPPED};
  bool control_registered = false;
  std::shared_ptr<broadcast_t> broadcast_ref;
  std::thread videoThread, audioThread;
  struct {void* peer = nullptr;} control;
  DESTRUCTOR
};
RETIREMENT
void publish(session_t& session) {PUBLICATION}
void enet_peer_disconnect_now(void*, int) {}
void control_step(control_server_t* server) {
  auto lg = server->_sessions.lock();
  for (auto pos = server->_sessions->begin(); pos != server->_sessions->end();) {
    auto session = *pos;
    CONTROL_RETIRE_BRANCH
    ++pos;
  }
}
}
int main(int argc, char** argv) {
  using namespace stream;
  assert(argc == 2); const std::string mode = argv[1];
  auto broadcast = std::make_shared<broadcast_t>();  // Another live session keeps broadcasting.
  auto &server = broadcast->control_server;
  auto owner = std::make_unique<session_t>();
  owner->broadcast_ref = broadcast;
  if (mode == "unregistered") {owner.reset(); return 0;}
  publish(*owner);
  if (mode == "peer-reused") {
    owner->control.peer = reinterpret_cast<void*>(1);
    owner->state.store(session::state_e::STOPPING);control_step(&server);
    session_t next;
    server._peer_to_session->emplace(owner->control.peer, &next);
    owner.reset();
    assert(server._peer_to_session->at(reinterpret_cast<void*>(1)) == &next);
    server._peer_to_session->clear();
    std::cout << "PASS peer-reused\n";return 0;
  }
  if (mode == "connected" || mode == "shutdown-tail") {
    owner->control.peer = reinterpret_cast<void*>(1);
    auto lg = server._peer_to_session.lock();
    server._peer_to_session->emplace(owner->control.peer, owner.get());
  }
  if (mode == "audio-started") {
    owner->audioThread = std::thread([shutdown=owner->shutdown_event] {shutdown->view();});
  }
  if (mode == "running-failure" || mode == "connected") owner->state.store(session::state_e::RUNNING);
  if (mode == "normal-joined") {owner->state.store(session::state_e::STOPPING);control_step(&server);}
  if (mode == "shutdown-tail") owner->controlEnd.raise(true);
  auto retiring = std::async(std::launch::async, [s=std::move(owner)]() mutable {s.reset();});
  const bool pending = retiring.wait_for(30ms) == std::future_status::timeout;
  // Before the fix, reset() returned with its freed pointer still registered.
  // This is the first state read made by the actual control STOPPING branch.
  control_step(&server);
  assert(retiring.wait_for(2s) == std::future_status::ready); retiring.get();
  assert(server._sessions->empty());
  assert(server._peer_to_session->empty());
  if (mode != "normal-joined" && mode != "shutdown-tail") assert(pending);
  // Retirement must not poison the shared server for a subsequent session.
  auto next = std::make_unique<session_t>();next->broadcast_ref=broadcast;publish(*next);
  next->state.store(session::state_e::STOPPING);control_step(&server);next.reset();
  std::cout << "PASS " << mode << '\n';
}
'''
for key, value in {'DESTRUCTOR': block(source, '~session_t()'), 'RETIREMENT': retirement,
                   'PUBLICATION': publication, 'CONTROL_RETIRE_BRANCH': stopping}.items():
    code = code.replace(key, value)
with tempfile.TemporaryDirectory(prefix='session-start-failure-') as directory:
    work = Path(directory)
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-g', '-pthread', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I' + str(ROOT), '-I' + os.environ.get('AUDIT_JSON_INCLUDE', '/usr/include'),
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    failures = 0
    modes = ['first-thread-failure', 'audio-started', 'running-failure', 'connected',
             'shutdown-tail', 'normal-joined', 'unregistered', 'peer-reused']
    for mode in modes:
        result = subprocess.run([str(work / 'test'), mode],
                                env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'), timeout=10)
        failures += result.returncode != 0
    print(f'{len(modes)-failures}/{len(modes)} session retirement cases passed', flush=True)
    raise SystemExit(failures != 0)
