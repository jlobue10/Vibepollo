/**
 * @file src/csrf_store.h
 * @brief Bounded per-client CSRF token store.
 */
#pragma once

#include <chrono>
#include <cstddef>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <string_view>

namespace confighttp {

  /**
   * CSRF tokens keyed by client identity (the remote address: an unauthenticated caller must not
   * be able to grow the store with arbitrary header values). The store is bounded: past the
   * capacity the entry that expires first is evicted, and expired entries are pruned on every
   * issue and validation.
   */
  class csrf_store_t {
  public:
    using clock = std::chrono::steady_clock;

    enum class validation_e {
      ok,
      missing,
      expired,
      mismatch,
    };

    csrf_store_t(std::size_t capacity, clock::duration lifetime, std::function<std::string()> generate):
        capacity_(capacity),
        lifetime_(lifetime),
        generate_(std::move(generate)) {}

    /** Returns the client's current token, issuing one when it has none or it expired. */
    std::string issue(std::string_view client_id, const clock::time_point now = clock::now()) {
      std::scoped_lock lock(mutex_);
      prune_locked(now);
      if (const auto existing = entries_.find(client_id); existing != entries_.end()) {
        return existing->second.token;
      }
      while (capacity_ != 0 && entries_.size() >= capacity_) {
        evict_oldest_locked();
      }
      auto token = generate_();
      entries_.emplace(std::string(client_id), entry_t {token, now + lifetime_});
      return token;
    }

    validation_e validate(std::string_view client_id, std::string_view token, const clock::time_point now = clock::now()) {
      std::scoped_lock lock(mutex_);
      const auto it = entries_.find(client_id);
      if (it == entries_.end()) {
        return validation_e::missing;
      }
      if (it->second.expiration < now) {
        entries_.erase(it);
        return validation_e::expired;
      }
      return it->second.token == token ? validation_e::ok : validation_e::mismatch;
    }

    void clear() {
      std::scoped_lock lock(mutex_);
      entries_.clear();
    }

    std::size_t size() const {
      std::scoped_lock lock(mutex_);
      return entries_.size();
    }

  private:
    struct entry_t {
      std::string token;
      clock::time_point expiration;
    };

    void prune_locked(const clock::time_point now) {
      for (auto it = entries_.begin(); it != entries_.end();) {
        it = it->second.expiration < now ? entries_.erase(it) : std::next(it);
      }
    }

    void evict_oldest_locked() {
      auto oldest = entries_.begin();
      for (auto it = entries_.begin(); it != entries_.end(); ++it) {
        if (it->second.expiration < oldest->second.expiration) {
          oldest = it;
        }
      }
      entries_.erase(oldest);
    }

    std::size_t capacity_;
    clock::duration lifetime_;
    std::function<std::string()> generate_;
    std::map<std::string, entry_t, std::less<>> entries_;
    mutable std::mutex mutex_;
  };

}  // namespace confighttp
