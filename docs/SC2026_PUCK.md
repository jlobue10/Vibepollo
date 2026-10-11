# Android Steam Controller 2026 puck support

The physical puck attaches to the Android client. Moonlight's new USB driver feeds
the existing controller/motion/touch/battery/haptic protocol; Windows continues to
emulate a wired Steam Controller through the VHF `vhf_sc26` profile.

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
