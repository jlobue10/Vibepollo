/**
 * @file src/server_cmd_budget.h
 * @brief Token bucket for client-triggered server commands.
 */
#pragma once

#include <chrono>
#include <cstddef>

namespace stream {

  /**
   * A client with the server_cmd permission used to start one thread and one process per
   * control message, unbounded. This admits a short burst and then one command per second.
   */
  class server_cmd_budget_t {
  public:
    using clock = std::chrono::steady_clock;

    explicit server_cmd_budget_t(std::size_t capacity = 4, std::chrono::milliseconds refill = std::chrono::seconds(1)):
        capacity_(capacity),
        refill_(refill),
        tokens_(capacity) {}

    bool admit(const clock::time_point now = clock::now()) {
      if (last_refill_ == clock::time_point {}) {
        last_refill_ = now;
      }
      while (tokens_ < capacity_ && now - last_refill_ >= refill_) {
        ++tokens_;
        last_refill_ += refill_;
      }
      if (tokens_ == capacity_) {
        last_refill_ = now;
      }
      if (tokens_ == 0) {
        ++rejected_;
        return false;
      }
      --tokens_;
      return true;
    }

    std::size_t rejected() const {
      return rejected_;
    }

  private:
    std::size_t capacity_;
    std::chrono::milliseconds refill_;
    std::size_t tokens_;
    std::size_t rejected_ {};
    clock::time_point last_refill_ {};
  };

}  // namespace stream
