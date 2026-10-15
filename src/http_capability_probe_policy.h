/**
 * @file src/http_capability_probe_policy.h
 * @brief Bounded wait for the background encoder capability probe behind
 *        /serverinfo and /applist.
 *
 * Moonlight picks the session codec from the /serverinfo it issues right
 * before /launch, so a cold-cache answer must prefer the real probe result.
 * The probe runs single-flight on its own worker; a discovery request waits
 * for it up to a bound and only then falls back to the baseline set.
 */
#pragma once

#include <chrono>
#include <optional>

namespace http_capability_probe {
  // Moonlight gives a /serverinfo request well over this before giving up,
  // and a healthy probe finishes within it; a stuck driver must not hold
  // discovery longer than this.
  inline constexpr auto kHttpCapabilityProbeWait = std::chrono::seconds(8);

  enum class wait_outcome_e {
    // The cache matched before the deadline; answer with the probe result.
    probe_complete,
    // The deadline passed while the probe was still running; answer baseline.
    expired,
    // The probe ended without a matching cache (failed or skipped); answer
    // baseline now rather than waiting out the deadline.
    probe_not_running,
  };

  /**
   * @brief Poll `probe_complete()` until it is true, the probe stops running,
   *        or the deadline passes. `now()` and `sleep()` are injectable.
   */
  template<class ProbeComplete, class ProbeRunning, class Now, class Sleep>
  wait_outcome_e wait_for_probe(
    ProbeComplete &&probe_complete,
    ProbeRunning &&probe_running,
    const std::chrono::steady_clock::time_point deadline,
    Now &&now,
    Sleep &&sleep
  ) {
    for (;;) {
      if (probe_complete()) {
        return wait_outcome_e::probe_complete;
      }
      if (!probe_running()) {
        return wait_outcome_e::probe_not_running;
      }
      if (now() >= deadline) {
        return wait_outcome_e::expired;
      }
      sleep();
    }
  }

  constexpr const char *wait_outcome_name(const wait_outcome_e outcome) {
    switch (outcome) {
      case wait_outcome_e::probe_complete:
        return "probe finished during the wait";
      case wait_outcome_e::expired:
        return "wait expired with the probe still running";
      case wait_outcome_e::probe_not_running:
        return "probe ended without a matching cache";
    }
    return "unknown";
  }
}  // namespace http_capability_probe
