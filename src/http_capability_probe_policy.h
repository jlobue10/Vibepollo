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
  // Moonlight's /serverinfo read timeout is 7 s (moonlight-android
  // NvHTTP.java READ_TIMEOUT = 7000, applied per read), and the host writes
  // nothing until this wait ends, so the wait plus one gate wait below must
  // end with margin inside it: a slow probe degrades the answer to the
  // baseline instead of failing the client's request. A healthy probe
  // finishes well within it; a stuck driver must not hold discovery longer.
  inline constexpr auto kHttpCapabilityProbeWait = std::chrono::seconds(5);
  // The probe worker, and the one request-side re-read after the worker ends,
  // take the stream lifecycle gate with this bounded wait. A discovery sample
  // owns the gate for a DXGI adapter enumeration (milliseconds), which the
  // worker must outwait; a session start can own it far longer, and the
  // worker yields to that instead of blocking its single-flight slot.
  inline constexpr auto kHttpCapabilityProbeGateWait = std::chrono::milliseconds(500);
  inline constexpr auto kHttpCapabilityProbeGatePoll = std::chrono::milliseconds(1);

  /**
   * @brief The wait deadline for a discovery request that arrived at
   *        `request_arrived`. The budget counts from arrival on the server,
   *        not from when a discovery worker picked the request up: time
   *        queued behind another handler is spent from the client's read
   *        timeout as well.
   */
  constexpr std::chrono::steady_clock::time_point wait_deadline(const std::chrono::steady_clock::time_point request_arrived) {
    return request_arrived + kHttpCapabilityProbeWait;
  }

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
