/**
 * @file src/control_replay_window.h
 * @brief Replay admission for authenticated v2 control packets.
 */
#pragma once

#include <bitset>
#include <cstddef>
#include <cstdint>

namespace stream {
  class control_replay_window {
  public:
    // The encryption sequence is global, but ENet orders each input channel
    // separately. Retain 64K sequence numbers (8 KiB per session) so a reliable
    // release can survive retransmission while mouse/sensor channels advance.
    static constexpr std::size_t capacity = 65536;

    // Call only after successful authentication. Sequence numbers do not wrap:
    // wrapping under the same key would reuse an AES-GCM nonce.
    bool accept(std::uint32_t sequence) {
      if (!initialized_) {
        initialized_ = true;
        highest_ = sequence;
      } else if (sequence > highest_) {
        received_ <<= sequence - highest_;
        highest_ = sequence;
      } else {
        const auto age = highest_ - sequence;
        if (age >= capacity || received_.test(age)) {
          return false;
        }
      }

      received_.set(highest_ - sequence);
      return true;
    }

  private:
    std::bitset<capacity> received_;
    std::uint32_t highest_ = 0;
    bool initialized_ = false;
  };
}  // namespace stream
