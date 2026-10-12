#!/usr/bin/env python3
"""Execute the production ICE SSE handler and worker lifecycle with real threads.

HTTP writes/authentication and the session registry are boundary fakes. Send
callbacks can be delayed, failed or withheld; late callbacks run under ASan.
--baseline extracts the handler from HEAD to reproduce the original leaks.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/confighttp.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/confighttp.cpp').read_text())
start = source.index('  void getWebRTCIceStream(')
end = source.index('\n  void getWebRTCCert(', start)
handler = source[start:end]
managed = 'sse_stream::workers_t &workers' in handler

code = r'''
#include <algorithm>
#include <atomic>
#include <barrier>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <future>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <nlohmann/json.hpp>
#include "sse_stream.h"
using namespace std::chrono_literals;
struct logger { template<class T> logger &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) logger()
namespace SimpleWeb {
  using error_code = std::error_code;
  using CaseInsensitiveMultimap = std::multimap<std::string, std::string>;
  enum class StatusCode { server_error_service_unavailable = 503 };
}
struct state_t {
  std::mutex mutex;
  std::condition_variable cv;
  bool destroyed = false, withheld = false, fail_send = false;
  int status = 0;
  SimpleWeb::CaseInsensitiveMultimap headers;
  std::vector<std::string> chunks;
  std::vector<std::function<void(const std::error_code &)>> callbacks;

  template<class Predicate> bool await(Predicate ready, std::chrono::milliseconds timeout = 2s) {
    std::unique_lock lock(mutex);
    return cv.wait_for(lock, timeout, ready);
  }
  void release() {
    std::vector<std::function<void(const std::error_code &)>> pending;
    {
      std::lock_guard lock(mutex);
      fail_send = true;
      withheld = false;
      pending.swap(callbacks);
    }
    for (auto &callback : pending) callback(std::make_error_code(std::errc::connection_reset));
  }
};
struct response_t : std::ostringstream {
  std::shared_ptr<state_t> state;
  bool close_connection_after_response = false;
  explicit response_t(std::shared_ptr<state_t> state): state(std::move(state)) {}
  ~response_t() {
    std::lock_guard lock(state->mutex);
    state->destroyed = true;
    state->cv.notify_all();
  }
  void write(const SimpleWeb::CaseInsensitiveMultimap &headers) {
    std::lock_guard lock(state->mutex);
    state->headers = headers;
    state->status = 200;
    state->cv.notify_all();
  }
  void write(SimpleWeb::StatusCode code, const std::string &body, const SimpleWeb::CaseInsensitiveMultimap &headers) {
    std::lock_guard lock(state->mutex);
    state->status = static_cast<int>(code);
    state->headers = headers;
    state->chunks.push_back(body);
    state->cv.notify_all();
  }
  void send(std::function<void(const std::error_code &)> callback) {
    bool fail;
    {
      std::lock_guard lock(state->mutex);
      state->chunks.push_back(str());
      str("");
      state->cv.notify_all();
      if (state->withheld) {
        state->callbacks.push_back(std::move(callback));
        return;
      }
      fail = state->fail_send;
    }
    callback(fail ? std::make_error_code(std::errc::connection_reset) : std::error_code {});
  }
};
struct request_t {
  std::vector<std::string> path_match;
  std::map<std::string, std::string> query;
  bool authorized = true;
  auto parse_query_string() const { return query; }
};
using resp_https_t = std::shared_ptr<response_t>;
using req_https_t = std::shared_ptr<request_t>;
bool authenticate(resp_https_t, req_https_t request) { return request->authorized; }
std::string get_cors_origin() { return "https://host"; }
void add_cors_headers(SimpleWeb::CaseInsensitiveMultimap &headers) { headers.emplace("Access-Control-Allow-Origin", get_cors_origin()); }
void bad_request(resp_https_t response, req_https_t, const std::string &) {
  std::lock_guard lock(response->state->mutex);
  response->state->status = 400;
}
namespace webrtc_stream {
  struct candidate_t { std::string mid; int mline_index; std::string candidate; std::size_t index; };
  std::mutex mutex;
  std::set<std::string> sessions;
  bool get_session(const std::string &id) { std::lock_guard lock(mutex); return sessions.count(id); }
  void set_session(const std::string &id, bool active) {
    std::lock_guard lock(mutex);
    if (active) sessions.insert(id); else sessions.erase(id);
  }
  std::vector<candidate_t> get_local_candidates(const std::string &id, std::size_t since) {
    if (id == "throw") throw std::runtime_error("injected candidate failure");
    if (id == "candidates" && since < 7) return {{"video", 0, "candidate:example", 7}};
    return {};
  }
}
namespace confighttp {
@@HANDLER@@
}
int checks = 0, failures = 0;
void check(bool ok, const char *name) {
  ++checks;
  if (!ok) { ++failures; std::cerr << "FAIL: " << name << '\n'; }
}
std::shared_ptr<state_t> invoke(sse_stream::workers_t &workers, const std::string &id,
                              bool withheld = false, std::string since = "0", bool authorized = true) {
  auto state = std::make_shared<state_t>();
  state->withheld = withheld;
  auto response = std::make_shared<response_t>(state);
  auto request = std::make_shared<request_t>();
  request->path_match = {"", id};
  request->query = {{"since", since}};
  request->authorized = authorized;
  @@INVOKE@@;
  return state;
}
void clean(const std::vector<std::shared_ptr<state_t>> &states) {
  for (auto &state : states) state->release();
  for (auto &state : states) check(state->await([&] { return state->destroyed; }, 4s), "test request released");
}
int main() {
  {
    sse_stream::workers_t workers;
    auto missing = invoke(workers, "missing");
    check(missing->status == 400 && missing->destroyed, "missing session rejected before worker creation");
    webrtc_stream::set_session("candidates", true);
    auto invalid = invoke(workers, "candidates", false, "invalid");
    check(invalid->status == 400 && invalid->destroyed, "invalid cursor rejected before worker creation");
    auto unauthorized = invoke(workers, "candidates", false, "0", false);
    check(unauthorized->status == 0 && unauthorized->destroyed, "unauthorized request never starts streaming");
    auto live = invoke(workers, "candidates");
    check(live->await([&] { return live->chunks.size() >= 3; }), "headers, prelude and candidate sent");
    {
      std::lock_guard lock(live->mutex);
      check(live->status == 200 && live->headers.count("Access-Control-Allow-Origin"), "SSE success keeps headers");
      check(live->chunks.size() >= 3 && live->chunks[1].size() == 2048 && live->chunks[1][0] == ':' &&
            live->chunks[2].find("id: 7\n") != std::string::npos &&
            live->chunks[2].find("candidate:example") != std::string::npos, "proxy prelude and candidate framing preserved");
    }
    check(live->await([&] { return live->chunks.size() >= 4; }, 3s), "healthy idle stream keeps connection alive");
    {
      std::lock_guard lock(live->mutex);
      check(live->chunks.size() == 4 && live->chunks.back().find("event: keepalive\n") != std::string::npos,
            "cursor prevents candidate replay between keepalives");
    }
    webrtc_stream::set_session("candidates", false);
    check(live->await([&] { return live->destroyed; }, 700ms), "session deletion retires its idle worker");
    clean({live});
  }
  {
    // Small limits exercise the same server-wide pool without 65 OS threads.
    sse_stream::workers_t workers(3, 2);
    webrtc_stream::set_session("cap-a", true);
    webrtc_stream::set_session("cap-b", true);
    std::vector<std::shared_ptr<state_t>> states;
    for (int i = 0; i < 3; ++i) states.push_back(invoke(workers, "cap-a", true));
    states.push_back(invoke(workers, "cap-b", true));
    states.push_back(invoke(workers, "cap-b", true));
    for (auto &state : states) check(state->await([&] { return state->status != 0; }), "admission decision completed");
    check(states[0]->status == 200 && states[1]->status == 200 && states[3]->status == 200, "available session/global slots admitted");
    check(states[2]->status == 503 && states[4]->status == 503, "per-session and global excess rejected");
    check(states[2]->headers.count("Retry-After") && states[4]->headers.count("Access-Control-Allow-Origin"),
          "rejection provides retry and CORS headers for polling fallback");
    webrtc_stream::set_session("cap-a", false);
    check(states[0]->await([&] { return states[0]->destroyed; }, 700ms) &&
          states[1]->await([&] { return states[1]->destroyed; }, 700ms), "session deletion cancels withheld send completions");
    auto reused = invoke(workers, "cap-b", true);
    check(reused->await([&] { return reused->status != 0; }) && reused->status == 200, "completed workers release capacity");
    states.push_back(reused);
    auto began = std::chrono::steady_clock::now();
    workers.stop();
    check(std::chrono::steady_clock::now() - began < 1s, "shutdown joins workers with withheld callbacks promptly");
    check(states[3]->destroyed && reused->destroyed, "shutdown releases every running response before returning");
    auto stopped = invoke(workers, "cap-b");
    check(stopped->await([&] { return stopped->status != 0; }) && stopped->status == 503 && stopped->destroyed,
          "shutdown rejects new event streams");
    states.push_back(stopped);
    // Completion is deliberately delivered AFTER worker destruction.
    clean(states);
  }
  {
    sse_stream::workers_t workers(8, 2);
    std::atomic<int> admitted {0};
    std::barrier launch {21};
    std::vector<std::jthread> requests;
    for (int i = 0; i < 20; ++i) requests.emplace_back([&] {
      launch.arrive_and_wait();
      if (workers.start("shared", [](std::stop_token stop) {
            while (!stop.stop_requested()) std::this_thread::sleep_for(1ms);
          })) ++admitted;
    });
    launch.arrive_and_wait();
    requests.clear();
    check(admitted == 2, "concurrent admission cannot exceed per-session bound");
    workers.stop();
  }
  {
    auto state = std::make_shared<state_t>();
    state->withheld = true;
    auto response = std::make_shared<response_t>(state);
    auto began = std::chrono::steady_clock::now();
    check(!sse_stream::flush(response, [] { return false; }, 20ms), "withheld callback has a finite send deadline");
    check(std::chrono::steady_clock::now() - began < 1s, "send deadline returns promptly");
    state->release();
    check(!sse_stream::flush(response, [] { return false; }), "send errors stop streaming");
    check(!sse_stream::flush(response, [] { return true; }), "cancelled stream does not enqueue another send");
  }
@@EXCEPTION_CASE@@
  check(@@SHUTDOWN_ORDER@@, "server drains ICE workers before stopping its I/O loop");
  std::cout << checks << " ICE event-stream checks, " << failures << " failures\n";
  return failures ? 1 : 0;
}
'''
exception_case = r'''
  {
    sse_stream::workers_t workers;
    webrtc_stream::set_session("throw", true);
    auto state = invoke(workers, "throw");
    check(state->await([&] { return state->destroyed; }), "worker exception releases response without terminating the host");
  }
'''
code = code.replace('@@HANDLER@@', handler)
code = code.replace('@@INVOKE@@', 'confighttp::getWebRTCIceStream(response, request' + (', workers' if managed else '') + ')')
# An uncaught exception in the old detached worker terminates the process;
# baseline focuses on resource lifetime instead, so all failures are reported.
code = code.replace('@@EXCEPTION_CASE@@', exception_case if managed else '')
shutdown_order = ('ice_stream_workers.stop();' in source and
                  source.index('ice_stream_workers.stop();') < source.index('    server.stop();'))
code = code.replace('@@SHUTDOWN_ORDER@@', 'true' if shutdown_order else 'false')
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory)
    (path / 'test.cpp').write_text(code)
    command = ['g++', '-std=c++20', '-pthread', '-g', '-O1', '-fsanitize=address,undefined',
               '-fno-omit-frame-pointer', '-I' + str(ROOT / 'src')]
    if os.environ.get('AUDIT_JSON_INCLUDE'):
        command += ['-I' + os.environ['AUDIT_JSON_INCLUDE']]
    subprocess.run(command + [str(path / 'test.cpp'), '-o', str(path / 'test')], check=True)
    environment = {**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'}
    result = subprocess.run([str(path / 'test')], env=environment, timeout=45)
    if result.returncode:
        sys.exit(result.returncode)
    subprocess.run(command + ['-I' + str(ROOT / 'third-party'),
                             str(ROOT / 'tests/input/test_sse_transport.cpp'), '-o', str(path / 'transport')], check=True)
    subprocess.run([str(path / 'transport')], env=environment, timeout=10, check=True)
