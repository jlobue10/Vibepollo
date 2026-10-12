# Input disconnect regression tests

`python3 tests/input/test_video_fec_wire.py` compiles the production video FEC
encoder and header writer with real Reed-Solomon under ASan/UBSan. It checks
8-bit percentage and 10-bit shard-index boundaries, declared versus actual
parity counts, padded/aligned payload reconstruction, and PyroWave's wire-budget
estimate. Initialize `third-party/nanors` and `third-party/moonlight-common-c`;
JSON headers are also required (`AUDIT_JSON_INCLUDE` may override their path).
`--baseline` reads production files from `HEAD`. No GPU or network is used.

`python3 tests/input/test_control_replay_window.py` executes the production v2
encrypted-control replay gate with authenticated packets delivered out of order
across independent ENet channels. It covers delayed keyboard/controller releases,
duplicate rejection, window edges and jumps, session isolation, and the 32-bit
sequence boundary. `--baseline` reads the gate from `HEAD`.

Run from the repository root:

```sh
python3 tests/input/test_delayed_mouse_release.py
python3 tests/input/test_disconnect_release.py
python3 tests/input/test_browser_disconnect.py
python3 tests/input/test_peer_state_bridge.py
python3 tests/input/test_gamepad_feedback.py
```

These scripts compile the production handlers with a deterministic task worker
and recording platform backends. They do not inject input into the test machine.
They cover all keycodes, changed mappings and flags, overlapping clients and
remaps, synthetic modifiers, repeat cancellation, all five mouse buttons,
controller removal and neutralization, touch/pen device lifetime, late packets,
independent browser input contexts sharing capture coordinates, and WebRTC
connection-loss/recovery callbacks.

The scripts do not replace a full platform build or driver-level testing.
`python3 tests/input/test_wasapi_silence.py` executes production WASAPI frame
assembly with a controlled capture clock. It checks silent-frame pacing at
5/10/20/60 ms for stereo, 5.1 and 7.1, partial-frame padding, resumed audio and
retained real samples under ASan/UBSan. `--baseline` reads HEAD. Actual endpoint
event timing and silent-to-active transitions still need Windows hardware checks.

`python3 tests/input/test_pyrowave_sync.py` compiles production D3D11 fence setup
and encode methods with the bundled PyroWave sync-object creation code. Fake
D3D/Vulkan boundaries cover handle ownership, allocation/import failures and a
conversion timeline that never signals. The fixture checks that this condition
does not submit a Vulkan dependency that could block device-idle teardown. It
uses ASan/UBSan and requires no GPU; real driver latency and device-loss recovery
still require a Windows integration run.
The feedback test executes production VHF/ViGEm dispatch against a real mailbox
with fake driver slots. It checks LED forwarding when rumble is disabled,
optional-category deduplication, first zero states after slot allocation, and
retry after a rejected queue insertion. It uses ASan/UBSan and needs
`nlohmann/json` headers (`AUDIT_JSON_INCLUDE` can override the include directory).
The WebRTC change includes the `third-party/libwebrtc` submodule's C bridge;
rebuild that dependency before linking Vibepollo (the cached library needs the
new `lwrtc_peer_register_state_callback` export).

## Windows integration check

Use both Moonlight and browser streaming, including two connected viewers:

1. Hold an ordinary key, then each of Alt/Ctrl/Shift/Win, and abruptly disconnect
   the client. Check that host `GetAsyncKeyState` reports the streamed keys up
   and that typing and Explorer double-click work normally.
2. Repeat with a keybinding remap, including an ordinary key mapped to another
   ordinary key and Alt mapped to Win. Change the mapping while the key is held;
   the originally pressed host key must be released.
3. Hold each mouse button, including X1 and X2, while disconnecting. Test a drag
   using absolute input and a disconnect immediately after releasing left-click.
4. Hold controller buttons, Back (before its Home timer expires), triggers and
   both sticks off-center. Disconnect the stream, and separately unplug one
   controller while another keeps sending packets. Verify neutral state/device
   removal and no delayed Home press. Repeat for ViGEm and VHF.
5. Disconnect during a touch drag, pen contact/hover with barrel buttons, and a
   controller touchpad contact. Verify that contacts and devices are removed.
6. Hold the same key from two clients. Disconnect one; the other must retain its
   key until release. Check that another client's key-up cannot release it.
7. With two browser viewers, disconnect the one holding input while the other
   continues streaming. Verify cleanup, working absolute coordinates in the
   remaining viewer, and no stale controller-slot allocation on reconnect.
8. Drop the browser client's network while holding input without closing its
   tab. Verify release when WebRTC reports disconnection, input recovery after
   reconnection, and session teardown if the peer fails.
9. Repeat disconnect while capture teardown is delayed. Input cleanup should
   run before the capture joins finish. Verify healthy Alt+Tab, typing, mouse,
   controller and pen/touch use after reconnect.

# VHF automatic profile fallback regression

`python3 tests/input/test_vhf_fallback.py` executes the production Windows
allocation and profile-selection functions with fake VHF/ViGEm driver objects.
It covers missing client-derived profiles under `auto` without ViGEm, explicit
profile preservation, failed automatic allocation, and ViGEm fallback.

`python3 tests/input/test_stream_packet_lifetime.py` runs production packet admission,
first session reads, packet metadata and encoder replacement publication against the
real mailbox queue under ASan/UBSan. The fake session excludes GPU/socket work.
Cases cover queued and in-flight audio/video at teardown, concurrent audio/video,
independent sessions, overflow/coalescing/stop, address reuse and encoder-owned
vector/NAL destruction. `--baseline` reads HEAD for before/after reproduction.

`python3 tests/input/test_ipc_pipe_contract.py` compiles the production framing
and WinPipe completion methods with fake transport/Win32 boundaries under
ASan/UBSan. It checks fragmented/coalesced frames, consumer buffer limits,
completion winning timeout cancellation, cancelled operations, broken pipes,
pending-I/O cleanup after a failed wait, and short overlapped writes.
`--baseline` reads HEAD to reproduce the pre-fix failures. Actual Windows pipe
scheduling and helper recovery still require a Windows integration run.

`python3 tests/input/test_playnite_path_guard.py` compiles the production Playnite media-path guard
(`is_safe_local_media_path`) and checks what it accepts (drive-rooted local files only: no UNC,
device or extended-length prefixes, no relative paths, `..`, empty segments or alternate data
streams), plus a text check that the cover conversion and the icon resolver call it before any
SYSTEM-side open. `--baseline` reads HEAD.

`python3 tests/input/test_anon_handshake_name.py` executes the production anonymous-pipe handshake
client against a peer whose 40-wchar pipe-name field has no terminator (ASan reported the over-read
before the fix) and checks that empty or path-like names keep the control pipe. `--baseline` reads
HEAD. No Windows pipes are opened.

`python3 tests/input/test_remote_monitor_lease_recovery.py` compiles the production Remote
Monitor coordinator (`src/remote_display_topology.cpp`) with fake runtime callbacks and checks
that a lease whose display was lost (driver watchdog failure, reinstall) is recreated on the next
resume or activation, that a present display is not recreated, and that `forget_all_leases`
(the watchdog-failure path) leads to recreation. `--baseline` reads HEAD. No platform display
code runs.

`python3 tests/input/test_find_match_bounds.py` compiles the production `find_match` (the
`$(...)` scanner behind `parse_env_val`) and runs it under ASan on an exact-size heap buffer
holding an unbalanced group: the previous loop read one element past the end before throwing.
`--baseline` reads HEAD.

`python3 tests/input/test_webrtc_option_bounds.py` is a text gate for the browser-facing WebRTC
inputs: session options bounded like the RTSP ANNOUNCE (width/height 16..8192, fps 10..1000,
bitrate 0..500000, audio channels 2/6/8), stored ICE candidates capped per session, and input
message integers read through `json_integer()` rather than float-to-int conversions.
`--baseline` reads HEAD.
