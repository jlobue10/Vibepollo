/**
 * @file src/deferred_launch_claim.h
 * @brief Single-claimant hand-off for an app launch deferred until a user session exists.
 *
 * proc_t::running() is polled by several threads (the stream control thread every
 * 5-150 ms, nvhttp workers, the web UI). Exactly one of them may resume a deferred
 * launch, and none of them may run it inline: the control thread raises controlEnd,
 * which join() waits for under the hang watchdog.
 */
#pragma once

#include <atomic>

namespace proc::deferred_launch {
  /**
   * @brief Claim the deferred launch. Returns true for exactly one caller while
   * `deferred` is set; the claimant clears `deferred` and owns `in_flight` until release().
   */
  inline bool claim(std::atomic<bool> &deferred, std::atomic<bool> &in_flight) {
    if (!deferred.load(std::memory_order_acquire)) {
      return false;
    }
    bool expected = false;
    if (!in_flight.compare_exchange_strong(expected, true, std::memory_order_acq_rel)) {
      return false;
    }
    deferred.store(false, std::memory_order_release);
    return true;
  }

  /// The launch ran (or was abandoned); another deferral may be claimed again later.
  inline void release(std::atomic<bool> &in_flight) {
    in_flight.store(false, std::memory_order_release);
  }

  /// The claimant could not start the launch (no thread): hand the deferral back for the next poll.
  inline void requeue(std::atomic<bool> &deferred, std::atomic<bool> &in_flight) {
    deferred.store(true, std::memory_order_release);
    in_flight.store(false, std::memory_order_release);
  }
}  // namespace proc::deferred_launch
