/**
 * @file src/login_backoff.h
 * @brief Per-remote-address failure backoff for credential checks.
 */
#pragma once

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <map>
#include <mutex>
#include <string>
#include <string_view>

namespace confighttp {

  /**
   * Counts failed credential checks per remote address. The first `free_failures` within the
   * window cost nothing; every further failure locks the address out for 2^n seconds (capped),
   * so an online dictionary attack against /api/auth/login degrades to a handful of guesses per
   * lockout instead of the io thread's full rate. A success clears the address. The table is
   * bounded: past the capacity the entry whose lockout ends first is dropped.
   */
  class login_backoff_t {
  public:
    using clock = std::chrono::steady_clock;

    struct decision_t {
      bool allowed;
      std::chrono::seconds retry_after;
    };

    explicit login_backoff_t(
      unsigned free_failures = 5,
      std::chrono::seconds window = std::chrono::minutes(10),
      std::chrono::seconds max_lockout = std::chrono::minutes(15),
      std::size_t capacity = 1024
    ):
        free_failures_(free_failures),
        window_(window),
        max_lockout_(max_lockout),
        capacity_(capacity) {}

    /** Whether the address may attempt a login now. */
    decision_t check(std::string_view address, const clock::time_point now = clock::now()) {
      std::scoped_lock lock(mutex_);
      const auto it = entries_.find(address);
      if (it == entries_.end() || it->second.locked_until <= now) {
        return {true, std::chrono::seconds::zero()};
      }
      const auto remaining = std::chrono::ceil<std::chrono::seconds>(it->second.locked_until - now);
      return {false, std::max(remaining, std::chrono::seconds(1))};
    }

    /** Records a failure; returns the lockout applied by this failure (zero while still free). */
    std::chrono::seconds note_failure(std::string_view address, const clock::time_point now = clock::now()) {
      std::scoped_lock lock(mutex_);
      auto it = entries_.find(address);
      if (it == entries_.end()) {
        while (capacity_ != 0 && entries_.size() >= capacity_) {
          evict_locked();
        }
        it = entries_.emplace(std::string(address), state_t {}).first;
        it->second.first_failure = now;
      } else if (it->second.locked_until <= now && now - it->second.first_failure > window_) {
        it->second = state_t {};
        it->second.first_failure = now;
      }
      auto &state = it->second;
      ++state.failures;
      if (state.failures <= free_failures_) {
        return std::chrono::seconds::zero();
      }
      const unsigned exponent = std::min<unsigned>(state.failures - free_failures_, 20);
      const auto lockout = std::min<std::chrono::seconds>(std::chrono::seconds(1LL << exponent), max_lockout_);
      state.locked_until = now + lockout;
      return lockout;
    }

    void note_success(std::string_view address) {
      std::scoped_lock lock(mutex_);
      if (const auto it = entries_.find(address); it != entries_.end()) {
        entries_.erase(it);
      }
    }

    std::size_t size() const {
      std::scoped_lock lock(mutex_);
      return entries_.size();
    }

  private:
    struct state_t {
      unsigned failures = 0;
      clock::time_point first_failure {};
      clock::time_point locked_until {};
    };

    void evict_locked() {
      auto victim = entries_.begin();
      for (auto it = entries_.begin(); it != entries_.end(); ++it) {
        if (it->second.locked_until < victim->second.locked_until) {
          victim = it;
        }
      }
      entries_.erase(victim);
    }

    unsigned free_failures_;
    std::chrono::seconds window_;
    std::chrono::seconds max_lockout_;
    std::size_t capacity_;
    std::map<std::string, state_t, std::less<>> entries_;
    mutable std::mutex mutex_;
  };

}  // namespace confighttp
