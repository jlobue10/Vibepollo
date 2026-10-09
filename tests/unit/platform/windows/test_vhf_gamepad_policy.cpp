/**
 * @file tests/unit/platform/windows/test_vhf_gamepad_policy.cpp
 * @brief Test the translation between normalized gamepad state and the VHF driver protocol.
 */
#include "../../../tests_common.h"

#include <algorithm>
#include <cmath>
#include <initializer_list>
#include <cstring>
#include <limits>

extern "C" {
#include <moonlight-common-c/src/Limelight.h>
}

#include <src/platform/windows/vhf_gamepad_policy.h>

namespace {

  using platf::vhf_gamepad::backend_e;
  using platf::vhf_gamepad::decode_rumble_rgb;
  using platf::vhf_gamepad::decode_steam_haptic;
  using platf::vhf_gamepad::steam_haptic_t;
  using platf::vhf_gamepad::synthesize_steam_rumble;
  using platf::vhf_gamepad::synthesized_rumble_t;
  using platf::vhf_gamepad::STEAM_HAPTIC_CLICK_HOLD_MS;
  using platf::vhf_gamepad::STEAM_HAPTIC_LEFT;
  using platf::vhf_gamepad::STEAM_HAPTIC_MIN_HOLD_MS;
  using platf::vhf_gamepad::STEAM_HAPTIC_RIGHT;
  using platf::vhf_gamepad::make_input_state;
  using platf::vhf_gamepad::normalized_state_t;
  using platf::vhf_gamepad::rumble_rgb_t;
  using platf::vhf_gamepad::select_automatic_backend;
  using platf::vhf_gamepad::select_automatic_profile;
  using platf::vhf_gamepad::supported_button_mask;
  using platf::vhf_gamepad::to_milli_units;
  using platf::vhf_gamepad::to_normalized_touch;
  using platf::vhf_gamepad::to_protocol_battery_state;
  using platf::vhf_gamepad::to_protocol_motion_kind;
  using platf::vhf_gamepad::to_protocol_touch_event;

  lvg::feedback_event make_rumble_event(
    const lvg::generic_rumble_rgb_feedback &payload,
    const lvg::feedback_type type = lvg::feedback_type::generic_rumble_rgb) {
    lvg::feedback_event event {};
    event.header.size = sizeof(event);
    event.header.version = lvg::k_protocol_version;
    event.controller_id = 3;
    event.type = type;
    event.payload_size = sizeof(payload);
    std::memcpy(event.payload, &payload, sizeof(payload));
    return event;
  }

  class VhfGamepadPolicyTest: public testing::Test {};

  TEST_F(VhfGamepadPolicyTest, AutomaticBackendFallsBackToVhfWhenVigemIsUnavailable) {
    EXPECT_EQ(select_automatic_backend(true, true), backend_e::vigem);
    EXPECT_EQ(select_automatic_backend(true, false), backend_e::vigem);
    EXPECT_EQ(select_automatic_backend(false, true), backend_e::vhf);
    EXPECT_EQ(select_automatic_backend(false, false), backend_e::unavailable);
  }

  TEST_F(VhfGamepadPolicyTest, AutomaticProfilePrefersXinputThenPlaystation) {
    const auto all_public =
      lvg::profile_bit(lvg::profile::xbox_series) |
      lvg::profile_bit(lvg::profile::dualsense) |
      lvg::profile_bit(lvg::profile::dualshock_4);
    EXPECT_EQ(select_automatic_profile(all_public), lvg::profile::xbox_series);
    EXPECT_EQ(
      select_automatic_profile(all_public & ~lvg::profile_bit(lvg::profile::xbox_series)),
      lvg::profile::dualsense);
    EXPECT_EQ(
      select_automatic_profile(lvg::profile_bit(lvg::profile::dualshock_4)),
      lvg::profile::dualshock_4);
  }

  TEST_F(VhfGamepadPolicyTest, AutomaticProfileNeverFallsBackToReservedGenericEnums) {
    const auto reserved_generics =
      lvg::profile_bit(lvg::profile::generic_pid) |
      lvg::profile_bit(lvg::profile::generic_hid);

    EXPECT_FALSE(select_automatic_profile(reserved_generics).has_value());
  }

  TEST_F(VhfGamepadPolicyTest, InputStateCarriesAValidProtocolHeader) {
    const auto request = make_input_state(5, normalized_state_t {});

    EXPECT_EQ(request.header.size, sizeof(lvg::input_state_request));
    EXPECT_EQ(request.header.version, lvg::k_protocol_version);
    EXPECT_EQ(request.header.reserved, 0);
    EXPECT_EQ(request.reserved, 0);
    EXPECT_EQ(request.controller_id, 5u);

    // The driver and client both reject a request that fails this predicate.
    EXPECT_TRUE(lvg::valid_request(&request, sizeof(request)));
  }

  TEST_F(VhfGamepadPolicyTest, ButtonsAreCopiedWithoutRemapping) {
    normalized_state_t state {};
    state.button_flags = supported_button_mask;

    EXPECT_EQ(make_input_state(0, state).buttons, supported_button_mask);
  }

  TEST_F(VhfGamepadPolicyTest, GripSenseBitsSurviveTheMask) {
    // Moonlight's LI_CCAP_GRIP_SENSE bits ride in the button word; the Steam Controller
    // profile follows the client's grips for good once it has seen one of them.
    normalized_state_t state {};
    state.button_flags = lvg::button_mask::left_grip_touch | lvg::button_mask::right_grip_touch;

    EXPECT_EQ(make_input_state(0, state).buttons,
              lvg::button_mask::left_grip_touch | lvg::button_mask::right_grip_touch);
  }

  TEST_F(VhfGamepadPolicyTest, StickTouchBitsSurviveTheMask) {
    normalized_state_t state {};
    state.button_flags = lvg::button_mask::left_stick_touch | lvg::button_mask::right_stick_touch;

    EXPECT_EQ(make_input_state(0, state).buttons,
              lvg::button_mask::left_stick_touch | lvg::button_mask::right_stick_touch);
  }

  TEST_F(VhfGamepadPolicyTest, UnknownButtonBitsAreDropped) {
    normalized_state_t state {};
    state.button_flags = lvg::button_mask::south | 0x00000800u | 0x80000000u;

    EXPECT_EQ(make_input_state(0, state).buttons, static_cast<std::uint32_t>(lvg::button_mask::south));
  }

  TEST_F(VhfGamepadPolicyTest, HorizontalAxesAndTriggersPassThrough) {
    normalized_state_t state {};
    state.left_x = -12000;
    state.right_x = 24000;
    state.left_trigger = 17;
    state.right_trigger = 255;

    const auto request = make_input_state(0, state);
    EXPECT_EQ(request.left_x, -12000);
    EXPECT_EQ(request.right_x, 24000);
    EXPECT_EQ(request.left_trigger, 17);
    EXPECT_EQ(request.right_trigger, 255);
  }

  TEST_F(VhfGamepadPolicyTest, VerticalAxesGoOverTheWireUnchanged) {
    // The wire contract is Vibepollo's normalized state, positive-up. Each driver profile
    // converts to its own device's convention, so converting here too inverted both sticks.
    normalized_state_t state {};
    state.left_y = 20000;
    state.right_y = -8000;

    const auto request = make_input_state(0, state);
    EXPECT_EQ(request.left_y, 20000);
    EXPECT_EQ(request.right_y, -8000);
  }

  TEST_F(VhfGamepadPolicyTest, ExtremeAxisValuesSurviveUnchanged) {
    normalized_state_t state {};
    state.left_y = std::numeric_limits<std::int16_t>::min();
    state.right_y = std::numeric_limits<std::int16_t>::max();

    const auto request = make_input_state(0, state);
    EXPECT_EQ(request.left_y, std::numeric_limits<std::int16_t>::min());
    EXPECT_EQ(request.right_y, std::numeric_limits<std::int16_t>::max());
  }

  TEST_F(VhfGamepadPolicyTest, RumbleAndRgbFeedbackIsDecoded) {
    const lvg::generic_rumble_rgb_feedback payload {0xAB00, 0x1200, 0x10, 0x20, 0x30, 0};

    rumble_rgb_t feedback {};
    ASSERT_TRUE(decode_rumble_rgb(make_rumble_event(payload), feedback));
    EXPECT_EQ(feedback.low_frequency, 0xAB00);
    EXPECT_EQ(feedback.high_frequency, 0x1200);
    EXPECT_EQ(feedback.red, 0x10);
    EXPECT_EQ(feedback.green, 0x20);
    EXPECT_EQ(feedback.blue, 0x30);
  }

  TEST_F(VhfGamepadPolicyTest, RumbleAndRgbFeedbackReportsAnLed) {
    const lvg::generic_rumble_rgb_feedback payload {1, 2, 3, 4, 5, 0};

    rumble_rgb_t feedback {};
    ASSERT_TRUE(decode_rumble_rgb(make_rumble_event(payload), feedback));
    EXPECT_TRUE(feedback.has_rgb);
  }

  TEST_F(VhfGamepadPolicyTest, ForceFeedbackRumbleCarriesNoLed) {
    // A DirectInput effect says nothing about a light. If this reported an LED, the zeroed
    // colour channels would switch off the light on the client's real controller.
    const lvg::generic_rumble_rgb_feedback payload {0x4000, 0x8000, 0, 0, 0, 0};

    rumble_rgb_t feedback {};
    ASSERT_TRUE(decode_rumble_rgb(
      make_rumble_event(payload, lvg::feedback_type::generic_rumble), feedback));
    EXPECT_EQ(feedback.low_frequency, 0x4000);
    EXPECT_EQ(feedback.high_frequency, 0x8000);
    EXPECT_FALSE(feedback.has_rgb);
  }

  TEST_F(VhfGamepadPolicyTest, ForceFeedbackRumbleIgnoresStrayColourBytes) {
    // The payload struct still has colour channels; a rumble-only event must not surface them
    // whatever they happen to contain.
    const lvg::generic_rumble_rgb_feedback payload {1, 2, 0xAA, 0xBB, 0xCC, 0};

    rumble_rgb_t feedback {};
    ASSERT_TRUE(decode_rumble_rgb(
      make_rumble_event(payload, lvg::feedback_type::generic_rumble), feedback));
    EXPECT_FALSE(feedback.has_rgb);
    EXPECT_EQ(feedback.red, 0);
    EXPECT_EQ(feedback.green, 0);
    EXPECT_EQ(feedback.blue, 0);
  }

  TEST_F(VhfGamepadPolicyTest, XboxRumbleCarriesAllFourActuators) {
    // The driver keeps one pending feedback slot, so body and trigger rumble arrive together;
    // splitting them across events would let coalescing drop one.
    const lvg::xbox_rumble_feedback payload {0x1000, 0x2000, 0x3000, 0x4000};

    lvg::feedback_event event {};
    event.header.size = sizeof(event);
    event.header.version = lvg::k_protocol_version;
    event.controller_id = 3;
    event.type = lvg::feedback_type::xbox_rumble;
    event.payload_size = sizeof(payload);
    std::memcpy(event.payload, &payload, sizeof(payload));

    rumble_rgb_t feedback {};
    ASSERT_TRUE(decode_rumble_rgb(event, feedback));
    EXPECT_EQ(feedback.low_frequency, 0x1000);
    EXPECT_EQ(feedback.high_frequency, 0x2000);
    EXPECT_EQ(feedback.left_trigger, 0x3000);
    EXPECT_EQ(feedback.right_trigger, 0x4000);
    EXPECT_TRUE(feedback.has_triggers);
    EXPECT_FALSE(feedback.has_rgb);
  }

  TEST_F(VhfGamepadPolicyTest, XboxRumbleWithAWrongSizedPayloadIsRejected) {
    lvg::feedback_event event {};
    event.header.size = sizeof(event);
    event.header.version = lvg::k_protocol_version;
    event.controller_id = 3;
    event.type = lvg::feedback_type::xbox_rumble;
    event.payload_size = sizeof(lvg::xbox_rumble_feedback) - 1;

    rumble_rgb_t feedback {};
    EXPECT_FALSE(decode_rumble_rgb(event, feedback));
  }

  TEST_F(VhfGamepadPolicyTest, GenericFeedbackReportsNoTriggerRumble) {
    const lvg::generic_rumble_rgb_feedback payload {1, 2, 3, 4, 5, 0};

    rumble_rgb_t feedback {};
    ASSERT_TRUE(decode_rumble_rgb(make_rumble_event(payload), feedback));
    EXPECT_FALSE(feedback.has_triggers);
    EXPECT_EQ(feedback.left_trigger, 0);
    EXPECT_EQ(feedback.right_trigger, 0);
  }

  TEST_F(VhfGamepadPolicyTest, PlaystationOutputCarriesRumbleLightbarAndTriggers) {
    lvg::playstation_output_feedback payload {};
    payload.low_frequency = 0x1100;
    payload.high_frequency = 0x2200;
    payload.red = 0xAA;
    payload.green = 0xBB;
    payload.blue = 0xCC;
    payload.valid = lvg::ps_output_lightbar_valid | lvg::ps_output_triggers_valid;
    payload.left_trigger.mode = static_cast<std::uint8_t>(lvg::trigger_effect_mode::weapon);
    payload.left_trigger.parameters[0] = 0x42;
    payload.right_trigger.mode = static_cast<std::uint8_t>(lvg::trigger_effect_mode::feedback);

    lvg::feedback_event event {};
    event.header.size = sizeof(event);
    event.header.version = lvg::k_protocol_version;
    event.controller_id = 1;
    event.type = lvg::feedback_type::playstation_output;
    event.payload_size = sizeof(payload);
    std::memcpy(event.payload, &payload, sizeof(payload));

    rumble_rgb_t feedback {};
    ASSERT_TRUE(decode_rumble_rgb(event, feedback));
    EXPECT_EQ(feedback.low_frequency, 0x1100);
    EXPECT_EQ(feedback.high_frequency, 0x2200);
    EXPECT_TRUE(feedback.has_rgb);
    EXPECT_EQ(feedback.red, 0xAA);
    EXPECT_TRUE(feedback.has_trigger_effects);
    // The client masks these bits when enabling the physical trigger programs.
    EXPECT_EQ(feedback.trigger_event_flags & DS_EFFECT_LEFT_TRIGGER, DS_EFFECT_LEFT_TRIGGER);
    EXPECT_EQ(feedback.trigger_event_flags & DS_EFFECT_RIGHT_TRIGGER, DS_EFFECT_RIGHT_TRIGGER);
    EXPECT_EQ(feedback.trigger_event_flags, 0x0C);
    EXPECT_EQ(feedback.left_effect.mode, static_cast<std::uint8_t>(lvg::trigger_effect_mode::weapon));
    EXPECT_EQ(feedback.left_effect.parameters[0], 0x42);
    EXPECT_EQ(feedback.right_effect.mode, static_cast<std::uint8_t>(lvg::trigger_effect_mode::feedback));
    // A PlayStation pad has no impulse triggers, so the Xbox rumble fields stay clear.
    EXPECT_FALSE(feedback.has_triggers);
  }

  TEST_F(VhfGamepadPolicyTest, PlaystationOutputWithoutLightbarLeavesTheLedAlone) {
    lvg::playstation_output_feedback payload {};
    payload.low_frequency = 0x0500;
    payload.red = 0xFF;  // Present in the struct but not claimed by the valid mask.

    lvg::feedback_event event {};
    event.header.size = sizeof(event);
    event.header.version = lvg::k_protocol_version;
    event.type = lvg::feedback_type::playstation_output;
    event.payload_size = sizeof(payload);
    std::memcpy(event.payload, &payload, sizeof(payload));

    rumble_rgb_t feedback {};
    ASSERT_TRUE(decode_rumble_rgb(event, feedback));
    EXPECT_FALSE(feedback.has_rgb);
    EXPECT_EQ(feedback.red, 0);
    EXPECT_FALSE(feedback.has_trigger_effects);
    EXPECT_EQ(feedback.trigger_event_flags, 0);
  }

  TEST_F(VhfGamepadPolicyTest, PlaystationTriggerReleaseStillEnablesBothEffectUpdates) {
    lvg::playstation_output_feedback payload {};
    payload.valid = lvg::ps_output_triggers_valid;

    lvg::feedback_event event {};
    event.type = lvg::feedback_type::playstation_output;
    event.payload_size = sizeof(payload);
    std::memcpy(event.payload, &payload, sizeof(payload));

    rumble_rgb_t feedback {};
    ASSERT_TRUE(decode_rumble_rgb(event, feedback));
    EXPECT_TRUE(feedback.has_trigger_effects);
    // Off is a program too: clearing the validity bits would leave the old effect active.
    EXPECT_EQ(feedback.trigger_event_flags, DS_EFFECT_LEFT_TRIGGER | DS_EFFECT_RIGHT_TRIGGER);
    EXPECT_EQ(feedback.left_effect.mode, 0);
    EXPECT_EQ(feedback.right_effect.mode, 0);
  }

  TEST_F(VhfGamepadPolicyTest, AdaptiveTriggerChangesAreNotDuplicateFeedback) {
    rumble_rgb_t previous {};
    previous.has_trigger_effects = true;
    previous.trigger_event_flags = DS_EFFECT_LEFT_TRIGGER | DS_EFFECT_RIGHT_TRIGGER;
    previous.left_effect.mode = static_cast<std::uint8_t>(lvg::trigger_effect_mode::weapon);
    previous.right_effect.mode = static_cast<std::uint8_t>(lvg::trigger_effect_mode::feedback);

    EXPECT_EQ(previous, previous);
    // raise_feedback discards equal reports before inspecting adaptive effects.
    auto changed = previous;
    changed.left_effect.mode = 0;
    EXPECT_FALSE(previous == changed);
    changed = previous;
    changed.right_effect.mode = 0;
    EXPECT_FALSE(previous == changed);
    changed = previous;
    changed.left_effect.parameters.back() = 0x42;
    EXPECT_FALSE(previous == changed);
    changed = previous;
    changed.right_effect.parameters.back() = 0x24;
    EXPECT_FALSE(previous == changed);
    changed = previous;
    changed.trigger_event_flags = DS_EFFECT_LEFT_TRIGGER;
    EXPECT_FALSE(previous == changed);
    changed = previous;
    changed.has_trigger_effects = false;
    EXPECT_FALSE(previous == changed);
  }

  TEST_F(VhfGamepadPolicyTest, TouchEventTypesMapToTheProtocol) {
    EXPECT_EQ(to_protocol_touch_event(LI_TOUCH_EVENT_DOWN),
              static_cast<std::uint8_t>(lvg::touch_event::down));
    EXPECT_EQ(to_protocol_touch_event(LI_TOUCH_EVENT_MOVE),
              static_cast<std::uint8_t>(lvg::touch_event::move));
    EXPECT_EQ(to_protocol_touch_event(LI_TOUCH_EVENT_UP),
              static_cast<std::uint8_t>(lvg::touch_event::up));
    // An unmapped event releases everything rather than inventing a contact.
    EXPECT_EQ(to_protocol_touch_event(0xEE),
              static_cast<std::uint8_t>(lvg::touch_event::cancel_all));
  }

  TEST_F(VhfGamepadPolicyTest, MotionAndBatteryMapToTheProtocol) {
    EXPECT_EQ(to_protocol_motion_kind(LI_MOTION_TYPE_ACCEL),
              static_cast<std::uint8_t>(lvg::motion_kind::accelerometer));
    EXPECT_EQ(to_protocol_motion_kind(LI_MOTION_TYPE_GYRO),
              static_cast<std::uint8_t>(lvg::motion_kind::gyroscope));
    EXPECT_EQ(to_protocol_motion_kind(0x7F), 0);

    EXPECT_EQ(to_protocol_battery_state(LI_BATTERY_STATE_CHARGING),
              static_cast<std::uint8_t>(lvg::battery_state::charging));
    EXPECT_EQ(to_protocol_battery_state(LI_BATTERY_STATE_FULL),
              static_cast<std::uint8_t>(lvg::battery_state::full));
  }

  TEST_F(VhfGamepadPolicyTest, TouchCoordinatesNormalizeAndClamp) {
    EXPECT_EQ(to_normalized_touch(0.0f), 0);
    EXPECT_EQ(to_normalized_touch(1.0f), 65535);
    EXPECT_EQ(to_normalized_touch(2.0f), 65535);
    EXPECT_EQ(to_normalized_touch(-1.0f), 0);
    // NaN must not become an arbitrary coordinate.
    EXPECT_EQ(to_normalized_touch(std::nanf("")), 0);
    const std::uint16_t middle = to_normalized_touch(0.5f);
    EXPECT_GT(middle, 32000);
    EXPECT_LT(middle, 33500);
  }

  TEST_F(VhfGamepadPolicyTest, MotionScalesToMilliUnits) {
    EXPECT_EQ(to_milli_units(1.0f), 1000);
    EXPECT_EQ(to_milli_units(-9.80665f), -9806);
    EXPECT_EQ(to_milli_units(std::nanf("")), 0);
    // Saturates instead of wrapping.
    EXPECT_EQ(to_milli_units(1e12f), 2147483000);
    EXPECT_EQ(to_milli_units(-1e12f), -2147483000);
  }

  TEST_F(VhfGamepadPolicyTest, NonRumbleFeedbackIsRejected) {
    auto event = make_rumble_event({});
    event.type = lvg::feedback_type::raw_output_report;

    rumble_rgb_t feedback {};
    EXPECT_FALSE(decode_rumble_rgb(event, feedback));
  }

  TEST_F(VhfGamepadPolicyTest, TruncatedFeedbackPayloadIsRejected) {
    auto event = make_rumble_event({});
    event.payload_size = sizeof(lvg::generic_rumble_rgb_feedback) - 1;

    rumble_rgb_t feedback {};
    EXPECT_FALSE(decode_rumble_rgb(event, feedback));
  }

  lvg::feedback_event make_steam_haptic_event(std::initializer_list<std::uint8_t> report) {
    lvg::steam_haptic_feedback payload {};
    payload.length = static_cast<std::uint8_t>(report.size());
    std::copy(report.begin(), report.end(), payload.report);

    lvg::feedback_event event {};
    event.header.size = sizeof(event);
    event.header.version = lvg::k_protocol_version;
    event.controller_id = 3;
    event.type = lvg::feedback_type::steam_haptic;
    event.payload_size = sizeof(payload);
    std::memcpy(event.payload, &payload, sizeof(payload));
    return event;
  }

  steam_haptic_t make_steam_haptic(std::initializer_list<std::uint8_t> report) {
    steam_haptic_t haptic {};
    haptic.length = static_cast<std::uint8_t>(report.size());
    std::copy(report.begin(), report.end(), haptic.report.begin());
    return haptic;
  }

  TEST_F(VhfGamepadPolicyTest, SteamHapticEventIsDecodedVerbatim) {
    steam_haptic_t haptic {};
    ASSERT_TRUE(decode_steam_haptic(make_steam_haptic_event({0x82, 0x01, 0x02, 0xf2}), haptic));
    EXPECT_EQ(haptic.length, 4);
    EXPECT_EQ(haptic.report[0], 0x82);
    EXPECT_EQ(haptic.report[1], 0x01);
    EXPECT_EQ(haptic.report[2], 0x02);
    EXPECT_EQ(haptic.report[3], 0xf2);

    rumble_rgb_t rumble {};
    EXPECT_FALSE(decode_rumble_rgb(make_steam_haptic_event({0x82, 0x01, 0x02, 0xf2}), rumble));

    auto truncated = make_steam_haptic_event({0x82, 0x01, 0x02, 0xf2});
    truncated.payload_size = sizeof(lvg::steam_haptic_feedback) - 1;
    EXPECT_FALSE(decode_steam_haptic(truncated, haptic));
    EXPECT_FALSE(decode_steam_haptic(make_steam_haptic_event({0x82}), haptic));
  }

  TEST_F(VhfGamepadPolicyTest, SteamHapticRumbleReportBecomesMotorSpeeds) {
    synthesized_rumble_t rumble {};
    EXPECT_EQ(synthesize_steam_rumble(
                make_steam_haptic({0x80, 0x00, 0x00, 0x00, 0x00, 0x80, 0x00, 0xff, 0x3f, 0x00}), rumble),
              STEAM_HAPTIC_LEFT | STEAM_HAPTIC_RIGHT);
    EXPECT_EQ(rumble.left, 0x8000);
    EXPECT_EQ(rumble.right, 0x3fff);
    EXPECT_EQ(rumble.left_hold_ms, STEAM_HAPTIC_RUMBLE_TIMEOUT_MS);
    EXPECT_EQ(rumble.right_hold_ms, STEAM_HAPTIC_RUMBLE_TIMEOUT_MS);
    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x80, 0x00, 0x00}), rumble), 0);
  }

  TEST_F(VhfGamepadPolicyTest, SteamHapticPulseBecomesDutyCycleForTheTrainsLength) {
    synthesized_rumble_t rumble {};
    // 10 ms on, 10 ms off, ten times, on the left pad: half magnitude for 200 ms.
    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x81, 0x01, 0x10, 0x27, 0x10, 0x27, 0x0a, 0x00}), rumble),
              STEAM_HAPTIC_LEFT);
    EXPECT_EQ(rumble.left, 32767);
    EXPECT_EQ(rumble.left_hold_ms, 200u);
    EXPECT_EQ(rumble.right, 0);

    // Steam's UI click: one 400 us pulse at full magnitude, held long enough to be felt.
    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x81, 0x00, 0x90, 0x01, 0x00, 0x00, 0x01, 0x00}), rumble),
              STEAM_HAPTIC_RIGHT);
    EXPECT_EQ(rumble.right, 65535);
    EXPECT_EQ(rumble.right_hold_ms, STEAM_HAPTIC_MIN_HOLD_MS);
    EXPECT_EQ(rumble.left, 32767);

    // A long train does not overflow 32-bit microseconds and is capped.
    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x81, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff}), rumble),
              STEAM_HAPTIC_RIGHT);
    EXPECT_EQ(rumble.right_hold_ms, 10'000u);

    // A zero-repeat pulse is Steam's per-side stop.
    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x81, 0x01, 0, 0, 0, 0, 0, 0}), rumble), STEAM_HAPTIC_LEFT);
    EXPECT_EQ(rumble.left, 0);
    EXPECT_EQ(rumble.left_hold_ms, 0u);
    EXPECT_EQ(rumble.right, 32767);
  }

  TEST_F(VhfGamepadPolicyTest, SteamHapticClickBecomesTimedRumbleFromItsGain) {
    synthesized_rumble_t rumble {};
    // Steam's test screen: a click at -14 dB on side 0 (right), every 100 ms.
    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x82, 0x00, 0x02, 0xf2}), rumble), STEAM_HAPTIC_RIGHT);
    EXPECT_NEAR(rumble.right, 65535 * 0.1995, 64);
    EXPECT_EQ(rumble.right_hold_ms, STEAM_HAPTIC_CLICK_HOLD_MS);
    EXPECT_EQ(rumble.left, 0);

    // A tone at +3 dB clips to full scale and runs until its off.
    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x82, 0x01, 0x03, 0x03}), rumble), STEAM_HAPTIC_LEFT);
    EXPECT_EQ(rumble.left, 65535);
    EXPECT_EQ(rumble.left_hold_ms, 0u);
    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x82, 0x01, 0x00, 0x00}), rumble), STEAM_HAPTIC_LEFT);
    EXPECT_EQ(rumble.left, 0);

    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x82, 0x00}), rumble), 0);
  }

  TEST_F(VhfGamepadPolicyTest, SteamHapticEffectsWithoutARumbleEquivalentAreNotRendered) {
    synthesized_rumble_t rumble {};
    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x83, 0, 0, 0, 0, 0, 0, 0, 0, 0}), rumble), 0);
    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x84, 0, 0, 0, 0, 0, 0, 0, 0}), rumble), 0);
    EXPECT_EQ(synthesize_steam_rumble(make_steam_haptic({0x85, 0, 0, 0}), rumble), 0);
    EXPECT_EQ(rumble.left, 0);
    EXPECT_EQ(rumble.right, 0);
  }


  TEST_F(VhfGamepadPolicyTest, SteamSplitPadsKeepExactlyOneOwnerPerHalf) {
    std::map<std::uint32_t, std::uint8_t> pointers;
    const auto send = [&](lvg::touch_event event, std::uint32_t pointer, float x) {
      return platf::vhf_gamepad::map_steam_touch(pointers, static_cast<std::uint8_t>(event), pointer, x);
    };
    EXPECT_EQ(send(lvg::touch_event::down, 10, .2f), 0);
    EXPECT_EQ(send(lvg::touch_event::down, 20, .8f), 1);
    for (std::uint32_t i = 100; i < 10000; ++i) {
      EXPECT_FALSE(send(lvg::touch_event::down, i, .1f));
      EXPECT_FALSE(send(lvg::touch_event::up, i, 0));
    }
    EXPECT_EQ(pointers.size(), 2);
    EXPECT_EQ(send(lvg::touch_event::move, 10, .9f), 0);
    EXPECT_EQ(send(lvg::touch_event::down, 10, .9f), 0);
    EXPECT_EQ(send(lvg::touch_event::up, 20, 0), 1);
    EXPECT_FALSE(send(lvg::touch_event::move, 20, 0));
    EXPECT_EQ(pointers.size(), 1);
    EXPECT_EQ(send(lvg::touch_event::down, 30, .9f), 1);
    EXPECT_EQ(send(lvg::touch_event::cancel_all, 0, 0), 0);
    EXPECT_TRUE(pointers.empty());
    EXPECT_FALSE(send(lvg::touch_event::down, 1, std::numeric_limits<float>::quiet_NaN()));
  }
}  // namespace
