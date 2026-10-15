/**
 * @file src/stream_start_gate_policy.h
 * @brief Bounded acquisition of the stream-start lifecycle gate.
 *
 * A stream start owns the lifecycle mutex only while no RTSP teardown is
 * pending. Without a deadline this waits forever, which is what an HTTP
 * /launch handler must never do: a stuck teardown or a long-running encoder
 * probe holding the mutex would otherwise hang the client's request.
 */
#pragma once

#include <chrono>
#include <mutex>
#include <optional>
#include <utility>

namespace stream_start_gate {
  enum class blocker_e {
    none,
    lifecycle_mutex,
    teardown_sessions,
  };

  template<class Mutex>
  struct acquire_result_t {
    std::unique_lock<Mutex> lock;
    blocker_e blocker {blocker_e::none};
    // Last observed pending-teardown count when the wait gave up.
    unsigned teardown_sessions {0};
    bool waited_for_teardown {false};

    [[nodiscard]] bool acquired() const {
      return lock.owns_lock();
    }
  };

  /**
   * @brief Acquire `mutex` while `teardown_count()` reads zero under it.
   *
   * Without a deadline this blocks on the mutex and polls the teardown counter
   * exactly like the unbounded gate always has. With a deadline the mutex is
   * polled with try_lock() so both the mutex wait and the teardown spin share
   * one budget; `now()` and `sleep()` are injectable for deterministic tests.
   */
  template<class Mutex, class TeardownCount, class Now, class Sleep>
  acquire_result_t<Mutex> acquire(
    Mutex &mutex,
    TeardownCount &&teardown_count,
    const std::optional<std::chrono::steady_clock::time_point> deadline,
    Now &&now,
    Sleep &&sleep
  ) {
    acquire_result_t<Mutex> result;
    for (;;) {
      std::unique_lock<Mutex> lock(mutex, std::defer_lock);
      if (!deadline) {
        lock.lock();
      } else if (!lock.try_lock()) {
        if (now() >= *deadline) {
          result.blocker = blocker_e::lifecycle_mutex;
          result.teardown_sessions = static_cast<unsigned>(teardown_count());
          return result;
        }
        sleep();
        continue;
      }

      const auto pending = static_cast<unsigned>(teardown_count());
      if (pending == 0) {
        result.lock = std::move(lock);
        result.blocker = blocker_e::none;
        return result;
      }

      result.waited_for_teardown = true;
      lock.unlock();
      if (deadline && now() >= *deadline) {
        result.blocker = blocker_e::teardown_sessions;
        result.teardown_sessions = pending;
        return result;
      }
      sleep();
    }
  }

  constexpr const char *blocker_name(const blocker_e blocker) {
    switch (blocker) {
      case blocker_e::lifecycle_mutex:
        return "lifecycle mutex held";
      case blocker_e::teardown_sessions:
        return "prior RTSP teardown still pending";
      case blocker_e::none:
        break;
    }
    return "none";
  }
}  // namespace stream_start_gate
