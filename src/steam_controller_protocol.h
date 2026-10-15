/**
 * @file src/steam_controller_protocol.h
 * @brief The Steam Controller (2026) extensions to the Moonlight protocol, in one place.
 * @details Vibepollo and the jlobue10 moonlight-common-c fork (the Android client) share these
 *          values; the moonlight-common-c submodule pinned here predates them, so each one is
 *          defined here as a fallback. A future submodule bump that defines a name with another
 *          value fails the `static_assert` below instead of silently masking the fallback: the
 *          values are wire-visible and the client fork's Limelight.h is the reference.
 */
#pragma once

#include <moonlight-common-c/src/Limelight.h>

// The client type a Steam Controller arrives as.
#ifndef LI_CTYPE_STEAM
  #define LI_CTYPE_STEAM 0x04
#endif
static_assert(LI_CTYPE_STEAM == 0x04, "LI_CTYPE_STEAM disagrees with the client fork");

// Capability bits in the controller arrival event.
#ifndef LI_CCAP_GRIP_SENSE
  #define LI_CCAP_GRIP_SENSE 0x200  // Reports capacitive grip touch via LEFT/RIGHT_GRIP_TOUCH_FLAG
#endif
static_assert(LI_CCAP_GRIP_SENSE == 0x200, "LI_CCAP_GRIP_SENSE disagrees with the client fork");
#ifndef LI_CCAP_STICK_TOUCH
  #define LI_CCAP_STICK_TOUCH 0x400  // Reports capacitive stick touch via LEFT/RIGHT_STICK_TOUCH_FLAG
#endif
static_assert(LI_CCAP_STICK_TOUCH == 0x400, "LI_CCAP_STICK_TOUCH disagrees with the client fork");
#ifndef LI_CCAP_STEAM_HAPTIC
  #define LI_CCAP_STEAM_HAPTIC 0x800  // Replays Steam Controller haptic reports on the real pads
#endif
static_assert(LI_CCAP_STEAM_HAPTIC == 0x800, "LI_CCAP_STEAM_HAPTIC disagrees with the client fork");
#ifndef LI_CCAP_SC26_BUTTONS
  #define LI_CCAP_SC26_BUTTONS 0x1000  // Reports independent physical pad and trigger clicks (*_CLICK_FLAG)
#endif
static_assert(LI_CCAP_SC26_BUTTONS == 0x1000, "LI_CCAP_SC26_BUTTONS disagrees with the client fork");
#ifndef LI_CCAP_STEAM_SETTINGS
  #define LI_CCAP_STEAM_SETTINGS 0x2000  // Applies Steam Controller settings writes to the real controller
#endif
static_assert(LI_CCAP_STEAM_SETTINGS == 0x2000, "LI_CCAP_STEAM_SETTINGS disagrees with the client fork");

// Button flag bits above MISC_FLAG, in the upper half of the 32-bit button word
// (buttonFlags2 on the wire). Grip and stick touch are held, not pressed.
#ifndef LEFT_GRIP_TOUCH_FLAG
  #define LEFT_GRIP_TOUCH_FLAG 0x400000
#endif
static_assert(LEFT_GRIP_TOUCH_FLAG == 0x400000, "LEFT_GRIP_TOUCH_FLAG disagrees with the client fork");
#ifndef RIGHT_GRIP_TOUCH_FLAG
  #define RIGHT_GRIP_TOUCH_FLAG 0x800000
#endif
static_assert(RIGHT_GRIP_TOUCH_FLAG == 0x800000, "RIGHT_GRIP_TOUCH_FLAG disagrees with the client fork");
#ifndef LEFT_STICK_TOUCH_FLAG
  #define LEFT_STICK_TOUCH_FLAG 0x1000000
#endif
static_assert(LEFT_STICK_TOUCH_FLAG == 0x1000000, "LEFT_STICK_TOUCH_FLAG disagrees with the client fork");
#ifndef RIGHT_STICK_TOUCH_FLAG
  #define RIGHT_STICK_TOUCH_FLAG 0x2000000
#endif
static_assert(RIGHT_STICK_TOUCH_FLAG == 0x2000000, "RIGHT_STICK_TOUCH_FLAG disagrees with the client fork");
#ifndef LEFT_PAD_CLICK_FLAG
  #define LEFT_PAD_CLICK_FLAG 0x04000000
#endif
static_assert(LEFT_PAD_CLICK_FLAG == 0x04000000, "LEFT_PAD_CLICK_FLAG disagrees with the client fork");
#ifndef RIGHT_PAD_CLICK_FLAG
  #define RIGHT_PAD_CLICK_FLAG 0x08000000
#endif
static_assert(RIGHT_PAD_CLICK_FLAG == 0x08000000, "RIGHT_PAD_CLICK_FLAG disagrees with the client fork");
#ifndef LEFT_TRIGGER_CLICK_FLAG
  #define LEFT_TRIGGER_CLICK_FLAG 0x10000000
#endif
static_assert(LEFT_TRIGGER_CLICK_FLAG == 0x10000000, "LEFT_TRIGGER_CLICK_FLAG disagrees with the client fork");
#ifndef RIGHT_TRIGGER_CLICK_FLAG
  #define RIGHT_TRIGGER_CLICK_FLAG 0x20000000
#endif
static_assert(RIGHT_TRIGGER_CLICK_FLAG == 0x20000000, "RIGHT_TRIGGER_CLICK_FLAG disagrees with the client fork");

// A haptic output report (0x80 rumble, 0x81 pulse, 0x82 command, 0x83 LFO tone, 0x84 log
// sweep, 0x85 script) forwarded verbatim, id first, to a LI_CCAP_STEAM_HAPTIC client.
#ifndef STEAM_HAPTIC_REPORT_MAX
  #define STEAM_HAPTIC_REPORT_MAX 12
#endif
static_assert(STEAM_HAPTIC_REPORT_MAX == 12, "STEAM_HAPTIC_REPORT_MAX disagrees with the client fork");

// A settings write forwarded to a LI_CCAP_STEAM_SETTINGS client: SET_SETTINGS_VALUES with
// 1..STEAM_SETTINGS_TRIPLES_MAX (id, lo, hi) triples, or LOAD_DEFAULT_SETTINGS with none.
#ifndef STEAM_SETTINGS_TRIPLES_MAX
  #define STEAM_SETTINGS_TRIPLES_MAX 9
#endif
static_assert(STEAM_SETTINGS_TRIPLES_MAX == 9, "STEAM_SETTINGS_TRIPLES_MAX disagrees with the client fork");
#ifndef STEAM_SETTINGS_PAYLOAD_SIZE
  #define STEAM_SETTINGS_PAYLOAD_SIZE (STEAM_SETTINGS_TRIPLES_MAX * 3)
#endif
static_assert(STEAM_SETTINGS_PAYLOAD_SIZE == 27, "STEAM_SETTINGS_PAYLOAD_SIZE disagrees with the client fork");
#ifndef STEAM_SETTINGS_SET_VALUES
  #define STEAM_SETTINGS_SET_VALUES 0x87
#endif
static_assert(STEAM_SETTINGS_SET_VALUES == 0x87, "STEAM_SETTINGS_SET_VALUES disagrees with the client fork");
#ifndef STEAM_SETTINGS_LOAD_DEFAULTS
  #define STEAM_SETTINGS_LOAD_DEFAULTS 0x8E
#endif
static_assert(STEAM_SETTINGS_LOAD_DEFAULTS == 0x8E, "STEAM_SETTINGS_LOAD_DEFAULTS disagrees with the client fork");

// Control stream packet types carrying the two (Vibepollo protocol extension). The client
// fork keeps them in ControlStream.c's packetTypes[] table rather than a header, so these
// names are Vibepollo's; the asserts still catch a header that grows them with another value.
#ifndef STEAM_HAPTIC_PACKET_TYPE
  #define STEAM_HAPTIC_PACKET_TYPE 0x5504
#endif
static_assert(STEAM_HAPTIC_PACKET_TYPE == 0x5504, "STEAM_HAPTIC_PACKET_TYPE disagrees with the client fork");
#ifndef STEAM_SETTINGS_PACKET_TYPE
  #define STEAM_SETTINGS_PACKET_TYPE 0x5505
#endif
static_assert(STEAM_SETTINGS_PACKET_TYPE == 0x5505, "STEAM_SETTINGS_PACKET_TYPE disagrees with the client fork");
