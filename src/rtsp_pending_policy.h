#pragma once

#include "remote_session.h"

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace rtsp_stream::pending_policy {
  constexpr int MAX_CAPTURE_FRAMERATE = 4000;

  struct normalized_framerate_t {
    int capture_framerate;
    int encoding_framerate;
    friend bool operator==(const normalized_framerate_t &, const normalized_framerate_t &) = default;
  };

  std::optional<normalized_framerate_t> normalize_requested_framerate(std::int64_t requested_framerate);
  std::optional<normalized_framerate_t> parse_requested_framerate(std::string_view requested_framerate);

  // Bounds for a client-announced video packetSize (the same range config::stream.packetsize
  // accepts). Below the minimum the broadcast shard arithmetic divides by zero or wraps.
  constexpr int PACKET_SIZE_MIN = 200;
  constexpr int PACKET_SIZE_MAX = 65535;

  // The announced video packetSize: digits only and within [PACKET_SIZE_MIN, PACKET_SIZE_MAX],
  // otherwise std::nullopt (the ANNOUNCE is answered 400).
  std::optional<int> parse_packet_size(std::string_view packet_size);

  // The announced Opus frame duration in milliseconds: exactly one of the frame sizes Opus
  // encodes (5, 10, 20, 40, 60), otherwise std::nullopt. Anything else made the encoder fail
  // and stop the process-wide audio packet queue, or overflowed the frame-size arithmetic.
  std::optional<int> parse_packet_duration(std::string_view packet_duration);

  enum class initial_route_e { reject, plaintext, encrypted };

  struct pending_owner_t {
    remote_session::role_e role {remote_session::role_e::game};
    std::string client_uuid;
    std::uint64_t generation {};
  };

  // Selects an unbound transport route from the first four wire bytes.  This
  // small policy seam is used by rtsp.cpp so NAT-mixed plaintext/encrypted
  // routing has direct component coverage.
  initial_route_e choose_initial_route(bool plaintext_available, bool encrypted_available, const std::array<std::uint8_t, 4> &first_word);
  bool game_session_requires_shutdown(bool game_runtime_active, remote_session::role_e role);
  bool control_server_should_remain_alive(bool game_runtime_active, bool has_processless_live_session, bool has_game_session_pending_or_draining);
  bool disconnect_scope_matches(remote_session::role_e candidate_role, remote_session::role_e requested_role, bool client_matches, bool all_clients);
  std::vector<pending_owner_t> expired_remote_input_owners(const std::vector<pending_owner_t> &expired);
  std::vector<pending_owner_t> disconnect_input_owners_to_forget(const std::vector<pending_owner_t> &removed);
}  // namespace rtsp_stream::pending_policy
