# Android Steam Controller 2026 puck support

The physical puck attaches to the Android client. Moonlight's new USB driver feeds
the existing controller/motion/touch/battery/haptic protocol; Windows continues to
emulate a wired Steam Controller through the VHF `vhf_steam` profile (plain `vhf` and
`auto` select it for a client that reports a Steam Controller).

Clients advertising `LI_CCAP_SC26_BUTTONS=0x1000` send independent pad/trigger clicks
in bits 26..29. The VHF adapter preserves those flags and sets driver metadata bit
30 only for this profile/capability. Untrusted client input cannot set metadata by
itself. All-released first states disable touch/trigger guesses in the new driver.
Old clients and old installed drivers retain their existing fallback behavior.

Use native paddles, unsplit dual trackpads and enabled motion on Android. See the
Android repo's `docs/SC2026_USB_PUCK.md` for setup and the physical acceptance matrix.

## Release dependency

The libvirtualgamepad source pin is updated in this PR for compilation and tests.
The bundled immutable driver package remains separately pinned. Before shipping
the new click fidelity, build and verify a producer release containing the driver
changes, then advance the package tag/hash/source/version together using the
existing pin-update tooling. Do not substitute a source SHA for a release hash.
The installed driver must also be updated. Puck gameplay input can use the existing
driver, but independent clicks and first-report capacitive fidelity require the
new one. No published/signed driver release or real puck validation is implied.

`tests/input/test_sc26_buttons.py` compiles the actual adapter and driver together
and checks all 16 physical click combinations, their releases, legacy behavior,
and capability/profile gating. It does not exercise Windows USB enumeration or RF.

## Firmware attributes and Steam's update notice

The virtual controller answers Steam's attribute reads (GET_ATTRIBUTES_VALUES 0x83,
GET_DEVICE_INFO 0xF2) with defaults captured from one wired unit, so Steam used to
show "controller firmware update available" for the streamed controller whenever those
defaults fell behind. A client advertising `LI_CCAP_STEAM_ATTRIBUTES=0x4000` reads the
real controller's attributes at link setup (USB cable, puck and BLE alike) and sends
them after the arrival event as the `SS_STEAM_CONTROLLER_ATTRIBUTES` input packet
(magic `0x55000008`, 84 bytes, every u32 little-endian): firmware and bootloader build
times, board revision, capabilities, radio/dongle firmware build times, build id and
the unit/board serials, with a mask saying which were actually read.

The host honours the packet only for a controller the arrival announced as
`LI_CTYPE_STEAM` with that capability, sanitises the strings (printable ASCII, at
most 15 characters) and hands the six fields the driver takes to the VHF driver's
`set_steam_controller_attributes` call. The radio and dongle build times are logged
only: the virtual device is a wired unit. An installed driver that predates the call
answers `ERROR_REVISION_MISMATCH`; the host logs one info line and the virtual
controller keeps its defaults. On success the host log shows what Steam will see:

```
VHF gamepad 0 (Steam Controller): real controller firmware build 2026-07-07 23:04:03 UTC (0x6a4d85e3), bootloader 2025-09-23 19:46:54 UTC (0x68d2f92e), board rev 74, build id 26e1f7ef1955, serial FXA..., via usb
```

Steam re-polls the attributes periodically, so a packet that arrives late is picked up.
Attributes are reset whenever the slot is recreated and are re-sent with the next
arrival.

> **Warning:** Do not start a Steam Controller firmware update from Steam while streaming: the update would target this virtual controller, not the real one. Update with the controller connected directly to a PC.
> Vibepollo logs this warning every time a Steam Controller slot is created.

`tests/input/test_steam_attributes_forward.py` compiles the production decode, gate and
relay with fake driver and slot boundaries.
