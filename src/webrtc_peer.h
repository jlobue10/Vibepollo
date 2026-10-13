/**
 * @file src/webrtc_peer.h
 * @brief Keep native signaling calls separate from peer teardown.
 */
#pragma once

#include <condition_variable>
#include <memory>
#include <mutex>
#include <utility>

#include <libwebrtc_c.h>

namespace webrtc_stream {
  class peer_t: public std::enable_shared_from_this<peer_t> {
  public:
    class call_t {
    public:
      call_t() = default;
      call_t(const call_t &) = delete;
      call_t &operator=(const call_t &) = delete;
      call_t(call_t &&other) noexcept:
          owner_(std::move(other.owner_)),
          handle_(std::exchange(other.handle_, nullptr)) {}

      ~call_t() {
        if (owner_) {
          std::lock_guard lock(owner_->mutex_);
          if (--owner_->calls_ == 0) {
            owner_->idle_.notify_all();
          }
        }
      }

      explicit operator bool() const {
        return handle_ != nullptr;
      }

      lwrtc_peer_t *get() const {
        return handle_;
      }

    private:
      friend class peer_t;
      call_t(std::shared_ptr<peer_t> owner, lwrtc_peer_t *handle):
          owner_(std::move(owner)),
          handle_(handle) {}

      std::shared_ptr<peer_t> owner_;
      lwrtc_peer_t *handle_ = nullptr;
    };

    explicit peer_t(lwrtc_peer_t *handle):
        handle_(handle) {}

    ~peer_t() {
      close();
    }

    call_t acquire() {
      std::lock_guard lock(mutex_);
      if (retired_ || !handle_) {
        return {};
      }
      auto owner = shared_from_this();
      ++calls_;
      return call_t(std::move(owner), handle_);
    }

    // Call from a teardown thread, never from inside a native callback/call.
    // The admission mutex is NOT held while calling libwebrtc: synchronous or
    // cross-thread callbacks may reenter acquire(), and must not deadlock.
    void close() {
      std::lock_guard close_lock(close_mutex_);
      std::unique_lock lock(mutex_);
      retired_ = true;
      idle_.wait(lock, [this]() { return calls_ == 0; });
      auto *handle = std::exchange(handle_, nullptr);
      lock.unlock();
      if (handle) {
        lwrtc_peer_close(handle);
        lwrtc_peer_release(handle);
      }
    }

  private:
    std::mutex mutex_;
    std::mutex close_mutex_;
    std::condition_variable idle_;
    lwrtc_peer_t *handle_;
    std::size_t calls_ = 0;
    bool retired_ = false;
  };
}  // namespace webrtc_stream
