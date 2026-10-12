// Exercise the real SimpleWeb response queue, including a callback that cannot
// run because the I/O loop has stopped. Only loopback HTTP is used, not TLS.
#include "sse_stream.h"
#include <Simple-Web-Server/server_http.hpp>
#include <iostream>

using namespace std::chrono_literals;
using server_t = SimpleWeb::Server<SimpleWeb::HTTP>;

struct observed_response_t {
  std::shared_ptr<server_t::Response> response;
  std::atomic<bool> &queued;
  template<class Callback> void send(Callback callback) {
    response->send(std::move(callback));
    queued.store(true);
  }
};

int main() {
  for (bool stop_io_first : {false, true}) {
    server_t server;
    sse_stream::workers_t workers;
    std::atomic<bool> handled {false}, queued {false}, finished {false};
    bool flushed = true;
    server.config.address = "127.0.0.1";
    server.config.port = 0;
    server.config.thread_pool_size = 0;
    server.resource["^/events$"]["GET"] = [&](auto response, auto) {
      if (!workers.start("session", [response, &queued, &finished, &flushed](std::stop_token stop) {
        response->close_connection_after_response = true;
        response->write({{"Content-Type", "text/event-stream"}});
        auto observed = std::make_shared<observed_response_t>(response, queued);
        flushed = sse_stream::flush(observed, [&] { return stop.stop_requested(); });
        finished.store(true);
      })) {
        throw std::runtime_error("worker admission unexpectedly failed");
      }
      handled.store(true);
    };
    unsigned short port = 0;
    server.start([&](unsigned short bound_port) { port = bound_port; });
    server.io_service->poll();
    if (!port) return 1;

    SimpleWeb::io_context client_io;
    SimpleWeb::HTTP client(client_io);
    client.connect({SimpleWeb::make_address("127.0.0.1"), port});
    const std::string request = "GET /events HTTP/1.1\r\nHost: localhost\r\n\r\n";
    SimpleWeb::asio::write(client, SimpleWeb::asio::buffer(request));
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while (!handled.load() && std::chrono::steady_clock::now() < deadline) {
      server.io_service->poll_one();
      std::this_thread::yield();
    }
    // Do not run another I/O handler. The next callback is deliberately queued
    // until AFTER the waiting stream worker has returned and been joined.
    while (!queued.load() && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
    if (!queued.load()) return 2;
    if (stop_io_first) server.stop();
    const auto began = std::chrono::steady_clock::now();
    workers.stop();
    if (!finished.load() || flushed || std::chrono::steady_clock::now() - began >= 1s) return 3;
    if (!stop_io_first) {
      // Run late completion and response deletion through the real queue.
      server.io_service->poll();
      server.stop();
    }
  }
  std::cout << "2 real SimpleWeb shutdown/late-completion cases passed\n";
}
