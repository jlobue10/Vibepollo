#!/usr/bin/env python3
"""Run production capture admission across a disconnect/join overlap.

The producer callback and reinit admission block are extracted verbatim from
video.cpp. Leases, queues and image events are real; GPU/display calls are faked.
Requires C++23 and nlohmann/json (AUDIT_JSON_INCLUDE may override its location).
Linux runs with ASan/UBSan; Windows runs the same assertions without sanitizers
because the CI MinGW toolchain does not provide their runtime libraries.
"""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / 'src/video.cpp').read_text()
start = source.index('      auto push_captured_image_callback =', source.index('  void captureThread('))
end = source.index('      auto status = disp->capture(', start)
callback = source[start:end]
# Include retired-consumer pruning when present, and still run against the
# pre-fix implementation to demonstrate the same handoff failure.
admission_start = source.index('            while (auto pending_context = capture_ctx_queue->pop(0ms)) {', end)
prune_start = source.find('            std::erase_if(capture_ctxs,', end, admission_start)
start = prune_start if prune_start != -1 else admission_start
end = source.index('            std::size_t display_retry_failures = 0;', start)
reinit_admission = source[start:end]

program = r'''
#include <chrono>
#include <future>
#include <iostream>
#include "src/video_capture_source.h"
using namespace std::chrono_literals;
namespace platf { struct img_t {}; }
namespace video::policy {
  std::optional<std::string> select_manual_display_output(int, int requested,
      const std::vector<std::string> &names) {
    if (requested < 0 || static_cast<std::size_t>(requested) >= names.size()) return {};
    return names[requested];
  }
}
using image_event = safe::event_t<std::shared_ptr<platf::img_t>>;
struct capture_ctx_t { std::shared_ptr<image_event> images; };
using queue_t = safe::queue_t<capture_ctx_t>;
struct source_ctx_t { std::shared_ptr<queue_t> queue; };
int start_source(source_ctx_t &ctx) { ctx.queue = std::make_shared<queue_t>(30); return 0; }
void stop_source(source_ctx_t &ctx) { ctx.queue->stop(); }
auto shared_source = safe::make_shared<source_ctx_t>(start_source, stop_source);
using lease_t = video::capture_source_lease_t<source_ctx_t>;
std::unique_ptr<lease_t> join() {
  return std::make_unique<lease_t>(video::capture_source_e::active_output,
    shared_source, start_source, stop_source);
}
struct display_t { int refreshes = 0; void request_refresh() { ++refreshes; } };
struct producer_t {
  std::shared_ptr<queue_t> capture_ctx_queue;
  std::vector<capture_ctx_t> capture_ctxs;
  std::shared_ptr<display_t> disp = std::make_shared<display_t>();
  bool event_driven_capture = true;
  bool artificial_reinit = false;
  std::shared_ptr<safe::event_t<int>> switch_display_event = std::make_shared<safe::event_t<int>>();
  std::uint64_t switch_display_generation = 0;
  std::optional<std::string> pending_switch_output;
  std::vector<std::string> display_names {"display-a", "display-b"};
  int manual_switch_selection = 0;
  bool reinit_advanced = false;
  bool push(bool frame_captured) {
''' + callback + r'''
    return push_captured_image_callback(std::make_shared<platf::img_t>(), frame_captured);
  }
  void admit_after_reinit() {
''' + reinit_admission + r'''
    reinit_advanced = true;
  }
};
int checks = 0, failures = 0;
void check(bool ok, const char *message) {
  ++checks;
  failures += !ok;
  std::cout << (ok ? "PASS " : "FAIL ") << message << '\n';
}
int main() {
  for (bool event_driven : {false, true}) {
    for (bool frame_captured : {false, true}) {
      auto departing = join();
      auto arriving = join(); // Scheduler pauses the new encoder before try_raise().
      producer_t producer {arriving->get()->queue};
      producer.event_driven_capture = event_driven;
      auto old_images = std::make_shared<image_event>();
      producer.capture_ctxs.push_back({old_images});
      old_images->stop();
      departing.reset();
      check(producer.capture_ctx_queue->running(), "the arriving lease keeps the producer queue alive");
      bool keep_capturing = producer.push(frame_captured);
      check(keep_capturing && producer.capture_ctxs.empty(),
        "retiring the last announced consumer does not terminate an admitted join");
      // captureThread's fail guard stops the queue when its callback ends capture.
      if (!keep_capturing) producer.capture_ctx_queue->stop();
      auto next_images = std::make_shared<image_event>();
      check(producer.capture_ctx_queue->try_raise(capture_ctx_t {next_images}),
        "the admitted encoder can still enqueue its images after the old encoder leaves");
      producer.push(false); // Event-driven admission must request a fresh frame.
      check(!event_driven || producer.disp->refreshes == 1,
        "a newly admitted event-driven consumer requests a refresh");
      producer.push(true);
      check(next_images->peek(), "the replacement consumer receives a captured frame");
      arriving.reset();
      check(!producer.push(false), "final lease release still stops the capture callback");
    }
  }
  {
    auto arriving = join();
    producer_t producer {arriving->get()->queue};
    auto retired = std::make_shared<image_event>();
    retired->stop();
    producer.capture_ctxs.push_back({retired});
    producer.capture_ctx_queue->try_raise(capture_ctx_t {retired});
    std::promise<void> entered;
    auto worker = std::async(std::launch::async, [&] {
      entered.set_value();
      producer.admit_after_reinit();
    });
    entered.get_future().wait();
    check(worker.wait_for(50ms) == std::future_status::timeout,
      "reinit waits through a gap between consumers while a source lease is alive");
    auto next_images = std::make_shared<image_event>();
    producer.capture_ctx_queue->try_raise(capture_ctx_t {next_images});
    worker.get();
    check(producer.reinit_advanced && producer.capture_ctxs.size() == 1 &&
        producer.capture_ctxs.front().images == next_images,
      "reinit resumes with a live consumer and ignores already retired arrivals");
  }
  {
    auto last = join();
    producer_t producer {last->get()->queue};
    std::promise<void> entered;
    auto worker = std::async(std::launch::async, [&] {
      entered.set_value();
      producer.admit_after_reinit();
    });
    entered.get_future().wait();
    last.reset();
    check(worker.wait_for(1s) == std::future_status::ready,
      "final lease release wakes an idle reinit worker");
    worker.get();
    check(!producer.reinit_advanced, "a stopped producer does not recreate its display");
  }
  {
    auto lease = join();
    producer_t producer {lease->get()->queue};
    auto retired = std::make_shared<image_event>();
    retired->stop();
    auto live = std::make_shared<image_event>();
    auto pending = std::make_shared<image_event>();
    producer.capture_ctxs = {{retired}, {live}};
    producer.capture_ctx_queue->try_raise(capture_ctx_t {pending});
    producer.admit_after_reinit();
    check(producer.reinit_advanced && producer.capture_ctxs.size() == 2,
      "reinit keeps active consumers and drains pending joins without waiting");
    check(producer.push(true) && live->peek() && pending->peek(),
      "remaining mirror consumers continue receiving the same captured frame");
  }
  {
    auto lease = join();
    producer_t producer {lease->get()->queue};
    producer.capture_ctxs.push_back({std::make_shared<image_event>()});
    producer.switch_display_event->raise(1);
    check(!producer.push(false) && producer.artificial_reinit &&
        producer.pending_switch_output == "display-b",
      "manual display switching still requests capture reinitialization");
  }
  std::cout << checks << " checks, " << failures << " failures\n";
  return failures ? 1 : 0;
}
'''
with tempfile.TemporaryDirectory(prefix='capture-consumer-handoff-') as temp:
    work = Path(temp)
    binary = work / ('test.exe' if os.name == 'nt' else 'test')
    (work / 'test.cpp').write_text(program)
    command = [os.environ.get('CXX', 'g++'), '-std=c++23', '-pthread', '-I', str(ROOT)]
    if os.name != 'nt':
        command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    if os.environ.get('AUDIT_JSON_INCLUDE'):
        command += ['-I', os.environ['AUDIT_JSON_INCLUDE']]
    subprocess.run(command + [str(work / 'test.cpp'), '-o', str(binary)], check=True)
    raise SystemExit(subprocess.run([str(binary)], timeout=10).returncode)
