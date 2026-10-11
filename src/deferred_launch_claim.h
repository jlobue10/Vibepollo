/**
 * @file src/deferred_launch_claim.h
 * @brief Nonblocking, generation-bound hand-off for a launch deferred until sign-in.
 *
 * Pollers never run commands inline. The launcher checks its ticket under the
 * lifecycle gate before touching app state; cancellation or a replacement launch
 * invalidates that ticket, even when the replacement has the same app id.
 */
#pragma once

#include <atomic>
#include <cstdint>
#include <optional>

namespace proc::deferred_launch {
  class state_t {
  public:
    using ticket_t = std::uint64_t;

    void defer() {
      replace(pending);
    }

    void cancel() {
      replace(idle);
    }

    bool active() const {
      return (state.load(std::memory_order_acquire) & phase_mask) != idle;
    }

    std::optional<ticket_t> claim() {
      auto expected = state.load(std::memory_order_acquire);
      if ((expected & phase_mask) != pending) {
        return std::nullopt;
      }
      const auto ticket = (expected & ~phase_mask) | launching;
      // Compare the pending generation itself. A poller delayed until after
      // another worker finishes must not claim its already-consumed request.
      if (!state.compare_exchange_strong(expected, ticket, std::memory_order_acq_rel)) {
        return std::nullopt;
      }
      return ticket;
    }

    bool is_current(ticket_t ticket) const {
      return state.load(std::memory_order_acquire) == ticket;
    }

    void release(ticket_t ticket) {
      state.compare_exchange_strong(ticket, ticket & ~phase_mask, std::memory_order_release);
    }

    void requeue(ticket_t ticket) {
      // Thread creation may fail after another thread cancelled/replaced the
      // app. Only return this request; never revive it over a newer generation.
      state.compare_exchange_strong(ticket, (ticket & ~phase_mask) | pending, std::memory_order_release);
    }

  private:
    static constexpr ticket_t idle = 0;
    static constexpr ticket_t pending = 1;
    static constexpr ticket_t launching = 2;
    static constexpr ticket_t phase_mask = 3;
    std::atomic<ticket_t> state {idle};

    void replace(ticket_t phase) {
      auto expected = state.load(std::memory_order_relaxed);
      while (!state.compare_exchange_weak(expected, ((expected & ~phase_mask) + 4) | phase,
                                          std::memory_order_acq_rel)) {}
    }
  };
}  // namespace proc::deferred_launch
