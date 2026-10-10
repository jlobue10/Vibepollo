/**
 * @file src/stream_packet.h
 * @brief Admission and lifetime fence for queued RTSP audio/video packets.
 */
#pragma once

#include <condition_variable>
#include <memory>
#include <mutex>
#include <utility>

namespace stream {
  class packet_channel_state_t;
  using packet_channel_t = std::shared_ptr<packet_channel_state_t>;

  // Packets retain this small channel, never the session or its broadcast owner.
  // Closing rejects queued packets and waits only for consumers already using
  // the session. Audio and video leases may coexist; no lock spans socket I/O.
  class packet_channel_state_t: public std::enable_shared_from_this<packet_channel_state_t> {
  public:
    class lease_t {
    public:
      lease_t() = default;
      lease_t(const lease_t &) = delete;
      lease_t &operator=(const lease_t &) = delete;
      lease_t(lease_t &&other) noexcept:
          _owner {std::move(other._owner)},
          _session {std::exchange(other._session, nullptr)} {
      }
      lease_t &operator=(lease_t &&) = delete;

      ~lease_t() {
        if (_owner) {
          std::lock_guard lock {_owner->_mutex};
          if (--_owner->_active == 0 && !_owner->_session) {
            _owner->_drained.notify_all();
          }
        }
      }

      void *get() const {
        return _session;
      }

    private:
      friend class packet_channel_state_t;
      lease_t(packet_channel_t owner, void *session):
          _owner {std::move(owner)},
          _session {session} {
      }
      packet_channel_t _owner;
      void *_session = nullptr;
    };

    explicit packet_channel_state_t(void *session):
        _session {session} {
    }

    [[nodiscard]] lease_t acquire() {
      std::lock_guard lock {_mutex};
      if (!_session) {
        return {};
      }
      auto owner = shared_from_this();
      ++_active;
      return {std::move(owner), _session};
    }

    // Call before releasing session fields, after capture producers have joined.
    // Idempotent, including channels whose capture never started. Queue overflow,
    // coalescing and stopped queues cannot strand the fence: only acquired
    // leases count, and their destructors release them on every consumer exit.
    void close() {
      std::unique_lock lock {_mutex};
      _session = nullptr;
      _drained.wait(lock, [this] { return _active == 0; });
    }

  private:
    std::mutex _mutex;
    std::condition_variable _drained;
    void *_session;
    unsigned int _active = 0;
  };
}  // namespace stream
