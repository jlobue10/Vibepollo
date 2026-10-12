/**
 * @file src/sse_stream.h
 * @brief Bounded, server-owned workers for long-lived event streams.
 */
#pragma once

#include <algorithm>
#include <atomic>
#include <chrono>
#include <future>
#include <list>
#include <memory>
#include <mutex>
#include <stop_token>
#include <string>
#include <thread>
#include <utility>

namespace sse_stream {
  class workers_t {
    struct worker_t {
      std::string session;
      std::atomic<bool> finished {false};
      std::jthread thread;
    };

    std::mutex mutex;
    bool stopping = false;
    const std::size_t total_limit;
    const std::size_t session_limit;
    // Stable entries let the thread publish completion without taking mutex.
    std::list<worker_t> workers;

  public:
    // A browser needs one subscription. Allow overlap during reconnection,
    // while bounding requests to both a single session and the entire server.
    explicit workers_t(std::size_t total_limit = 64, std::size_t session_limit = 4):
        total_limit(total_limit), session_limit(session_limit) {}

    ~workers_t() {
      stop();
    }

    template<class Task>
    bool start(const std::string &session, Task task) {
      std::lock_guard lock(mutex);
      if (stopping) {
        return false;
      }
      workers.remove_if([](const auto &worker) { return worker.finished.load(); });
      const auto session_workers = static_cast<std::size_t>(std::count_if(workers.begin(), workers.end(), [&](const auto &worker) { return worker.session == session; }));
      if (workers.size() >= total_limit || session_workers >= session_limit) {
        return false;
      }

      auto &worker = workers.emplace_back();
      try {
        worker.session = session;
        worker.thread = std::jthread([&worker, task = std::move(task)](std::stop_token stop) mutable {
          // The caller handles/logs exceptions. Destroy its captures (including
          // the HTTP response) before publishing completion to a reaper.
          {
            auto run = std::move(task);
            run(stop);
          }
          worker.finished.store(true);
        });
      } catch (...) {
        workers.pop_back();
        return false;
      }
      return true;
    }

    void stop() {
      std::list<worker_t> pending;
      {
        std::lock_guard lock(mutex);
        stopping = true;
        for (auto &worker : workers) {
          worker.thread.request_stop();
        }
        pending.splice(pending.end(), workers);
      }
      // jthread joins outside mutex. Every worker gets cancellation before any
      // join, and the server must remain alive until all responses are released.
    }
  };

  template<class Response, class Cancelled>
  bool flush(const std::shared_ptr<Response> &response, Cancelled cancelled,
             std::chrono::steady_clock::duration timeout = std::chrono::seconds(30)) {
    if (cancelled()) {
      return false;
    }
    // SimpleWeb may discard a callback when its event loop shuts down. A late
    // callback must also remain safe after cancellation or the send deadline.
    auto completion = std::make_shared<std::promise<bool>>();
    auto result = completion->get_future();
    response->send([completion](const auto &ec) {
      completion->set_value(!ec);
    });
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (!cancelled()) {
      const auto next_check = std::min(deadline, std::chrono::steady_clock::now() + std::chrono::milliseconds(100));
      if (result.wait_until(next_check) == std::future_status::ready) {
        return result.get() && !cancelled();
      }
      if (std::chrono::steady_clock::now() >= deadline) {
        return false;
      }
    }
    return false;
  }
}  // namespace sse_stream
