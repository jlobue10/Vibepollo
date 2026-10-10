#include "stream_protocol.h"

#include <algorithm>

namespace stream {
  std::size_t video_send_batch_size(std::size_t block_size, std::size_t prefix_size, std::size_t max_batch_bytes) {
    const auto byte_limit = std::min<std::size_t>(64 * 1024, max_batch_bytes);
    // Even an individual packet larger than the configured batch budget must
    // still be sent. Check before adding so invalid sizes cannot overflow.
    if (block_size > byte_limit || prefix_size > byte_limit - block_size || block_size + prefix_size == 0) {
      return 1;
    }
    return std::clamp<std::size_t>(byte_limit / (block_size + prefix_size), 1, 64);
  }

  std::optional<control_packet_view_t> decode_control_packet(std::string_view packet_bytes) {
    if (packet_bytes.size() < sizeof(std::uint16_t)) {
      return std::nullopt;
    }
    const auto lo = static_cast<std::uint8_t>(packet_bytes[0]);
    const auto hi = static_cast<std::uint8_t>(packet_bytes[1]);
    const auto type = static_cast<std::uint16_t>(lo | (static_cast<std::uint16_t>(hi) << 8));
    return control_packet_view_t {type, packet_bytes.substr(sizeof(type))};
  }

  std::vector<std::uint8_t> concat_and_insert(
    std::uint64_t insert_size,
    std::uint64_t slice_size,
    std::string_view data1,
    std::string_view data2
  ) {
    if (slice_size == 0) {
      return {};
    }
    // One pass straight from the two inputs: a PyroWave frame is 0.5-1 MB at up
    // to 120 fps and this runs on the broadcast thread that also paces the send,
    // so the intermediate joined copy was ~100-200 MB/s of memcpy and a large
    // allocation per frame for nothing.
    const std::size_t total = data1.size() + data2.size();
    const auto slices = (total + slice_size - 1) / slice_size;
    std::vector<std::uint8_t> result;
    result.reserve(total + slices * insert_size);
    std::size_t offset = 0;
    while (offset < total) {
      result.insert(result.end(), insert_size, 0);
      std::size_t remaining = std::min<std::size_t>(slice_size, total - offset);
      while (remaining) {
        // The slice may straddle the boundary between data1 and data2.
        const bool first = offset < data1.size();
        const std::string_view src = first ? data1 : data2;
        const std::size_t at = first ? offset : offset - data1.size();
        const std::size_t count = std::min(remaining, src.size() - at);
        result.insert(result.end(), src.begin() + at, src.begin() + at + count);
        offset += count;
        remaining -= count;
      }
    }
    return result;
  }
}  // namespace stream
