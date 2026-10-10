#!/usr/bin/env python3
"""Run the production recv_ping against a session whose shutdown is raised mid-wait.

The wait used to block in messages->pop for the whole ping_timeout regardless of the
session's shutdown, which join()'s 10 s watchdog then counted against the video thread.
Socket plumbing is faked; the queue, fail_guard and the loop are the production ones.
--baseline runs the source at HEAD (the shutdown case then takes the full timeout).
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
baseline = '--baseline' in sys.argv
source = (subprocess.check_output(['git', 'show', 'HEAD:src/stream.cpp'], cwd=ROOT, text=True, encoding='utf-8')
          if baseline else (ROOT / 'src/stream.cpp').read_text(encoding='utf-8'))

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

recv_ping = block(source, 'int recv_ping(session_t *session,')
code = r"""
#include <algorithm>
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <variant>
#include "src/thread_safe.h"
#include "src/utility.h"
using namespace std::literals;
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) Log{}
namespace mail { constexpr std::string_view shutdown = "shutdown"; }
namespace asio::ip { struct address { std::string text; std::string to_string() const { return text; } bool operator==(const address &o) const { return text == o.text; } }; }
namespace udp { struct endpoint { asio::ip::address addr; int p {0}; asio::ip::address address() const { return addr; } int port() const { return p; } }; }
enum class socket_e : int { video, audio };
constexpr int ML_FF_SESSION_ID_V1 = 0x01;
using av_session_id_t = std::variant<asio::ip::address, std::string>;
using message_queue_t = std::shared_ptr<safe::queue_t<std::pair<udp::endpoint, std::string>>>;
struct ctx_t {
  std::atomic<std::uint64_t> video_recv_count {0}, audio_recv_count {0};
  struct mqq_t {
    message_queue_t last;
    int raises {0};
    template<class K> void raise(socket_e, K, message_queue_t m) { ++raises; if (m) last = m; }
  };
  std::shared_ptr<mqq_t> message_queue_queue = std::make_shared<mqq_t>();
};
struct broadcast_t { using ptr_t = std::shared_ptr<ctx_t>; } broadcast;
namespace config { struct { std::chrono::milliseconds ping_timeout {5000}; } stream; }
struct session_t {
  struct { int mlFeatureFlags {0}; } config;
  safe::mail_t mail = std::make_shared<safe::mail_raw_t>();
  safe::mail_raw_t::event_t<bool> shutdown_event = mail->event<bool>(::mail::shutdown);
};
RECV_PING
int main(int argc, char **argv) {
  assert(argc == 2);
  const std::string mode = argv[1];
  session_t session;
  auto ctx = std::make_shared<ctx_t>();
  udp::endpoint peer;
  const auto t0 = std::chrono::steady_clock::now();
  auto elapsed_ms = [&] { return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count(); };
  auto push = [&](std::string payload) {
    while (!ctx->message_queue_queue->last) std::this_thread::sleep_for(1ms);
    ctx->message_queue_queue->last->raise(std::pair {udp::endpoint {{"10.0.0.2"}, 48000}, std::move(payload)});
  };
  int rc = 99;
  if (mode == "shutdown") {
    std::thread stopper([&] { std::this_thread::sleep_for(50ms); session.shutdown_event->raise(true); });
    rc = recv_ping(&session, ctx, socket_e::video, "PINGPAYLOAD"sv, peer, 5000ms);
    stopper.join();
    assert(rc == -1);
    assert(elapsed_ms() < 1500);  // HEAD: the full 5 s
  } else if (mode == "ping") {
    std::thread pinger([&] { std::this_thread::sleep_for(50ms); push("xxPINGPAYLOADxx"); });
    rc = recv_ping(&session, ctx, socket_e::video, "PINGPAYLOAD"sv, peer, 5000ms);
    pinger.join();
    assert(rc == 0 && peer.port() == 48000 && elapsed_ms() < 1500);
  } else if (mode == "junk-then-ping") {
    std::thread pinger([&] { push("hello"); std::this_thread::sleep_for(150ms); push("PINGPAYLOAD"); });
    rc = recv_ping(&session, ctx, socket_e::audio, "PINGPAYLOAD"sv, peer, 5000ms);
    pinger.join();
    assert(rc == 0 && elapsed_ms() < 1500);
  } else if (mode == "legacy-ping") {
    session.config.mlFeatureFlags = 0;
    std::thread pinger([&] { push("PING"); });
    rc = recv_ping(&session, ctx, socket_e::audio, "OTHER"sv, peer, 5000ms);
    pinger.join();
    assert(rc == 0);
  } else if (mode == "timeout") {
    rc = recv_ping(&session, ctx, socket_e::video, "PINGPAYLOAD"sv, peer, 300ms);
    assert(rc == -1 && elapsed_ms() >= 290 && elapsed_ms() < 1500);
  } else {
    std::cerr << "unknown mode " << mode << '\n';
    return 2;
  }
  // the fail_guard stopped the queue and unregistered it
  assert(ctx->message_queue_queue->last && !ctx->message_queue_queue->last->running());
  std::cout << "PASS " << mode << " (" << elapsed_ms() << " ms)\n";
  return 0;
}
"""
code = code.replace('RECV_PING', recv_ping)
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code, encoding='utf-8')
    subprocess.run(['g++', '-std=c++20', '-g', '-pthread', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I' + str(ROOT), '-I' + os.environ.get('AUDIT_JSON_INCLUDE', '/usr/include'), str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    modes = ['shutdown', 'ping', 'junk-then-ping', 'legacy-ping', 'timeout']
    failures = 0
    for mode in modes:
        result = subprocess.run([str(work / 'test'), mode], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'), timeout=30)
        failures += result.returncode != 0
    print(f'{len(modes)-failures}/{len(modes)} ping wait cases passed', flush=True)
    raise SystemExit(bool(failures))
