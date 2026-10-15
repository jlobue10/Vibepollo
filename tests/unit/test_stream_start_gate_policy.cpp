#include "src/stream_start_gate_policy.h"

#include <atomic>
#include <chrono>
#include <gtest/gtest.h>
#include <mutex>
#include <thread>

namespace {
  using namespace std::chrono_literals;
  using stream_start_gate::blocker_e;

  // Deterministic clock: every sleep() advances the clock by one tick.
  struct fake_clock_t {
    std::chrono::steady_clock::time_point now {std::chrono::steady_clock::time_point {} + 1s};
    unsigned sleeps {0};

    auto now_fn() {
      return [this] {
        return now;
      };
    }

    auto sleep_fn(const std::chrono::milliseconds tick = 1ms) {
      return [this, tick] {
        ++sleeps;
        now += tick;
      };
    }
  };
}  // namespace

TEST(StreamStartGatePolicy, AcquiresImmediatelyWhenIdle) {
  std::mutex gate;
  std::atomic_uint teardown {0};
  fake_clock_t clock;

  auto result = stream_start_gate::acquire(
    gate,
    [&] { return teardown.load(); },
    clock.now + 20s,
    clock.now_fn(),
    clock.sleep_fn()
  );

  ASSERT_TRUE(result.acquired());
  EXPECT_EQ(result.blocker, blocker_e::none);
  EXPECT_FALSE(result.waited_for_teardown);
  EXPECT_EQ(clock.sleeps, 0u);
  result.lock.unlock();
  EXPECT_TRUE(gate.try_lock());
  gate.unlock();
}

TEST(StreamStartGatePolicy, UnboundedWaitSpinsUntilTeardownCompletes) {
  std::mutex gate;
  std::atomic_uint teardown {2};
  fake_clock_t clock;
  unsigned samples = 0;

  auto result = stream_start_gate::acquire(
    gate,
    [&] {
      // Teardown finishes after a few samples; the unbounded gate must keep polling.
      if (++samples >= 3) {
        teardown.store(0);
      }
      return teardown.load();
    },
    std::nullopt,
    clock.now_fn(),
    clock.sleep_fn()
  );

  ASSERT_TRUE(result.acquired());
  EXPECT_TRUE(result.waited_for_teardown);
  EXPECT_EQ(clock.sleeps, 2u);
}

TEST(StreamStartGatePolicy, ReportsTeardownBlockerAtDeadline) {
  std::mutex gate;
  std::atomic_uint teardown {1};
  fake_clock_t clock;

  auto result = stream_start_gate::acquire(
    gate,
    [&] { return teardown.load(); },
    clock.now + 5ms,
    clock.now_fn(),
    clock.sleep_fn()
  );

  EXPECT_FALSE(result.acquired());
  EXPECT_EQ(result.blocker, blocker_e::teardown_sessions);
  EXPECT_EQ(result.teardown_sessions, 1u);
  EXPECT_TRUE(result.waited_for_teardown);
  // The mutex is released again so the stuck teardown can still finish.
  EXPECT_TRUE(gate.try_lock());
  gate.unlock();
}

TEST(StreamStartGatePolicy, ReportsMutexBlockerAtDeadline) {
  std::mutex gate;
  std::atomic_uint teardown {0};
  fake_clock_t clock;
  std::unique_lock<std::mutex> holder(gate);

  auto result = stream_start_gate::acquire(
    gate,
    [&] { return teardown.load(); },
    clock.now + 5ms,
    clock.now_fn(),
    clock.sleep_fn()
  );

  EXPECT_FALSE(result.acquired());
  EXPECT_EQ(result.blocker, blocker_e::lifecycle_mutex);
  EXPECT_FALSE(result.waited_for_teardown);
  EXPECT_GE(clock.sleeps, 5u);
}

TEST(StreamStartGatePolicy, AcquiresOnceMutexIsReleasedBeforeDeadline) {
  std::mutex gate;
  std::atomic_uint teardown {0};
  fake_clock_t clock;
  std::unique_lock<std::mutex> holder(gate);

  auto result = stream_start_gate::acquire(
    gate,
    [&] { return teardown.load(); },
    clock.now + 20s,
    clock.now_fn(),
    [&] {
      ++clock.sleeps;
      clock.now += 1ms;
      if (clock.sleeps == 3 && holder.owns_lock()) {
        holder.unlock();
      }
    }
  );

  ASSERT_TRUE(result.acquired());
  EXPECT_EQ(result.blocker, blocker_e::none);
  EXPECT_EQ(clock.sleeps, 3u);
}

TEST(StreamStartGatePolicy, TeardownCountThenReleaseAcquiresBeforeDeadline) {
  std::mutex gate;
  std::atomic_uint teardown {1};
  fake_clock_t clock;

  std::thread finisher([&] {
    std::this_thread::sleep_for(10ms);
    teardown.store(0);
  });
  auto result = stream_start_gate::acquire(
    gate,
    [&] { return teardown.load(); },
    std::chrono::steady_clock::now() + 20s,
    [] { return std::chrono::steady_clock::now(); },
    [] { std::this_thread::sleep_for(1ms); }
  );
  finisher.join();

  ASSERT_TRUE(result.acquired());
  EXPECT_TRUE(result.waited_for_teardown);
}

TEST(StreamStartGatePolicy, BlockerNamesAreStable) {
  EXPECT_STREQ(stream_start_gate::blocker_name(blocker_e::lifecycle_mutex), "lifecycle mutex held");
  EXPECT_STREQ(stream_start_gate::blocker_name(blocker_e::teardown_sessions), "prior RTSP teardown still pending");
  EXPECT_STREQ(stream_start_gate::blocker_name(blocker_e::none), "none");
}

// Bounded wait for the background HTTP capability probe (same fake-clock style).
#include "src/http_capability_probe_policy.h"

namespace {
  using http_capability_probe::wait_outcome_e;
}

TEST(HttpCapabilityProbeWaitPolicy, AnswersImmediatelyWhenCacheAlreadyMatches) {
  fake_clock_t clock;
  const auto outcome = http_capability_probe::wait_for_probe(
    [] { return true; },
    [] { return true; },
    clock.now + http_capability_probe::kHttpCapabilityProbeWait,
    clock.now_fn(),
    clock.sleep_fn()
  );
  EXPECT_EQ(outcome, wait_outcome_e::probe_complete);
  EXPECT_EQ(clock.sleeps, 0u);
}

TEST(HttpCapabilityProbeWaitPolicy, WaitsUntilProbeCompletesBeforeDeadline) {
  fake_clock_t clock;
  unsigned samples = 0;
  const auto outcome = http_capability_probe::wait_for_probe(
    [&] { return ++samples >= 4; },
    [] { return true; },
    clock.now + http_capability_probe::kHttpCapabilityProbeWait,
    clock.now_fn(),
    clock.sleep_fn(100ms)
  );
  EXPECT_EQ(outcome, wait_outcome_e::probe_complete);
  EXPECT_EQ(clock.sleeps, 3u);
}

TEST(HttpCapabilityProbeWaitPolicy, ExpiresWhileProbeStillRunning) {
  fake_clock_t clock;
  const auto deadline = clock.now + http_capability_probe::kHttpCapabilityProbeWait;
  const auto outcome = http_capability_probe::wait_for_probe(
    [] { return false; },
    [] { return true; },
    deadline,
    clock.now_fn(),
    clock.sleep_fn(100ms)
  );
  EXPECT_EQ(outcome, wait_outcome_e::expired);
  EXPECT_GE(clock.now, deadline);
  EXPECT_EQ(clock.sleeps, static_cast<unsigned>(http_capability_probe::kHttpCapabilityProbeWait / 100ms));
}

TEST(HttpCapabilityProbeWaitPolicy, WaitFitsInsideMoonlightReadTimeout) {
  // moonlight-android NvHTTP.java READ_TIMEOUT = 7000 ms per read, and the
  // host writes nothing until the wait ends. The request-side re-read after
  // the worker ends may add one gate wait on top.
  EXPECT_LT(http_capability_probe::kHttpCapabilityProbeWait + http_capability_probe::kHttpCapabilityProbeGateWait, 7s);
  EXPECT_LT(http_capability_probe::kHttpCapabilityProbeGatePoll, http_capability_probe::kHttpCapabilityProbeGateWait);
}

TEST(HttpCapabilityProbeWaitPolicy, DeadlineCountsFromRequestArrival) {
  fake_clock_t clock;
  // Queued 3 s behind another discovery handler before being picked up.
  const auto arrived = clock.now - 3s;
  const auto deadline = http_capability_probe::wait_deadline(arrived);
  EXPECT_EQ(deadline, arrived + http_capability_probe::kHttpCapabilityProbeWait);
  const auto outcome = http_capability_probe::wait_for_probe(
    [] { return false; },
    [] { return true; },
    deadline,
    clock.now_fn(),
    clock.sleep_fn(100ms)
  );
  EXPECT_EQ(outcome, wait_outcome_e::expired);
  EXPECT_EQ(clock.sleeps, static_cast<unsigned>((http_capability_probe::kHttpCapabilityProbeWait - 3s) / 100ms));
}

TEST(HttpCapabilityProbeWaitPolicy, ArrivalPastDeadlineStillSamplesOnce) {
  fake_clock_t clock;
  unsigned samples = 0;
  const auto outcome = http_capability_probe::wait_for_probe(
    [&] { ++samples; return false; },
    [] { return true; },
    http_capability_probe::wait_deadline(clock.now - 10s),
    clock.now_fn(),
    clock.sleep_fn(100ms)
  );
  EXPECT_EQ(outcome, wait_outcome_e::expired);
  EXPECT_EQ(samples, 1u);
  EXPECT_EQ(clock.sleeps, 0u);
}

TEST(HttpCapabilityProbeWaitPolicy, StopsEarlyWhenProbeEndsWithoutCache) {
  fake_clock_t clock;
  unsigned samples = 0;
  const auto outcome = http_capability_probe::wait_for_probe(
    [] { return false; },
    [&] { return ++samples < 3; },
    clock.now + http_capability_probe::kHttpCapabilityProbeWait,
    clock.now_fn(),
    clock.sleep_fn(100ms)
  );
  EXPECT_EQ(outcome, wait_outcome_e::probe_not_running);
  EXPECT_EQ(clock.sleeps, 2u);
}

TEST(HttpCapabilityProbeWaitPolicy, OutcomeNamesAreStable) {
  EXPECT_STREQ(http_capability_probe::wait_outcome_name(wait_outcome_e::probe_complete), "probe finished during the wait");
  EXPECT_STREQ(http_capability_probe::wait_outcome_name(wait_outcome_e::expired), "wait expired with the probe still running");
  EXPECT_STREQ(http_capability_probe::wait_outcome_name(wait_outcome_e::probe_not_running), "probe ended without a matching cache");
}
