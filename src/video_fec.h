/**
 * @file src/video_fec.h
 * @brief Video FEC percentage and parity-count arithmetic shared with the planner.
 */
#pragma once

#include <algorithm>
#include <cstddef>

namespace video_fec {
  // fecInfo dedicates bits 4..11 to the percentage. Higher bits hold the shard
  // index, so even a small block's negotiated parity minimum must respect this.
  constexpr std::size_t MAX_PERCENTAGE = 255;

  inline std::size_t percentage_for_minimum(std::size_t data, std::size_t percentage, std::size_t minimum) {
    if (!data || !percentage) {
      return 0;
    }
    if (minimum) {
      // Smallest integer p such that ceil(data * p / 100) >= minimum.
      percentage = std::max(percentage, (100 * (minimum - 1)) / data + 1);
    }
    return std::min(percentage, MAX_PERCENTAGE);
  }

  inline std::size_t parity_shards(std::size_t data, std::size_t percentage) {
    return (data * percentage + 99) / 100;
  }
}  // namespace video_fec
