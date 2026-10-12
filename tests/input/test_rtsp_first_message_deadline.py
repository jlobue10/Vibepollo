#!/usr/bin/env python3
"""Run the production RTSP socket class over real Boost.Asio loopback TCP sockets.

The first-message deadline is shortened from 10 s to 100 ms. RTSP parsing and
framing are real; launch reservation, crypto results, logging and dispatch are
boundary fakes. Partial reads must neither cancel nor renew the deadline.
--baseline extracts HEAD. Requires Boost headers and the common-C submodule.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/rtsp.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/rtsp.cpp').read_text())
policy = (ROOT / 'src/rtsp_pending_policy.cpp').read_text()


def block(src, marker):
    start = src.index(marker)
    end = src.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (src[end] == '{') - (src[end] == '}')
    return src[start:end + 1]


code = r'''
#define BOOST_BIND_GLOBAL_PLACEHOLDERS
#include <utility>
#include <boost/asio.hpp>
#include <boost/bind.hpp>
#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cstdint>
#include <functional>
#include <iostream>
#include <memory>
#include <string>
#include <string_view>
#include <vector>
extern "C" {
#include "Rtsp.h"
}
using boost::asio::ip::tcp;
using namespace std::literals;
struct null_log { template<class T> null_log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) null_log()
namespace util {
  template<class F> struct guard {
    F fn; bool active=true; ~guard() { if (active) fn(); } void disable() { active=false; }
  };
  template<class F> guard<F> fail_guard(F f) { return {std::move(f)}; }
  template<class It> int from_chars(It first, It last) {
    int value=0; std::from_chars(first,last,value); return value;
  }
  namespace endian {
    template<class T> T big(T value) { return static_cast<T>(htonl(static_cast<uint32_t>(value))); }
  }
}
namespace crypto { using aes_t=std::vector<uint8_t>; }
struct cipher_t {
  int decrypt(std::string_view, std::vector<uint8_t> &out, crypto::aes_t *) {
    const std::string text="OPTIONS * RTSP/1.0\r\nCSeq: 1\r\n\r\n";
    out.assign(text.begin(),text.end()); return 0;
  }
};
struct launch_session_t { std::shared_ptr<cipher_t> rtsp_cipher; };
struct free_msg { void operator()(RTSP_MESSAGE *msg) const { freeMessage(msg); delete msg; } };
using msg_t=std::unique_ptr<RTSP_MESSAGE,free_msg>;
void print_msg(PRTSP_MESSAGE) {}
void respond(tcp::socket &, launch_session_t &, POPTION_ITEM, int, const char *, int, const std::string_view &) {}
namespace pending_policy {
  enum class initial_route_e { reject, plaintext, encrypted };
  @@ROUTE@@
}
#pragma pack(push,1)
@@HEADER@@;
#pragma pack(pop)
@@SOCKET@@;
static int checks, failures;
static void check(bool ok, const char *name) {
  ++checks; failures+=!ok; std::cout<<(ok?"PASS ":"FAIL ")<<name<<'\n';
}
struct connection {
  boost::asio::io_context io;
  tcp::acceptor listener {io,{tcp::v4(),0}};
  tcp::socket peer {io};
  std::shared_ptr<socket_t> server;
  int dispatched=0;
  explicit connection(int mode) {
    peer.connect(listener.local_endpoint());
    server=std::make_shared<socket_t>(io,[this](std::shared_ptr<socket_t>,msg_t &&){++dispatched;});
    listener.accept(server->sock);
    auto plain=std::make_shared<launch_session_t>();
    auto encrypted=std::make_shared<launch_session_t>(); encrypted->rtsp_cipher=std::make_shared<cipher_t>();
    if(mode==0 || mode==1) {
      server->plaintext_candidate=plain; server->encrypted_candidates.push_back(encrypted);
      server->reserve_plaintext_candidate=[](const auto &p){return p;};
      server->reserve_encrypted_candidate=[](const auto &p){return p;};
    } else server->session=(mode==2 ? encrypted : plain);
    server->read();
  }
  void write(std::string_view data) {
    boost::system::error_code ec; boost::asio::write(peer,boost::asio::buffer(data.data(),data.size()),ec);
  }
  void run() {
    // Keep the event loop running beyond the deadline, even after dispatch.
    boost::asio::steady_timer keep_alive(io,250ms);
    keep_alive.async_wait([](const boost::system::error_code &){});
    io.run_for(300ms);
  }
  ~connection() {
    boost::system::error_code ec; peer.close(ec); server->sock.close(ec); server->idle_timer.cancel();
    io.restart(); io.poll();
  }
};
std::string encrypted_message(size_t payload=16) {
  encrypted_rtsp_header_t header {};
  header.typeAndLength=htonl(encrypted_rtsp_header_t::ENCRYPTED_MESSAGE_TYPE_BIT|payload);
  header.sequenceNumber=htonl(1);
  return std::string(reinterpret_cast<const char *>(&header),sizeof(header))+std::string(payload,'x');
}
int main() {
  const auto encrypted=encrypted_message();
  {
    connection c(1); c.run();
    check(!c.server->sock.is_open() && c.server->encrypted_candidates.empty() && !c.server->plaintext_candidate,
          "an idle unbound connection expires and releases pending launch candidates");
  }
  for (size_t prefix : {size_t(4),sizeof(encrypted_rtsp_header_t)}) {
    connection c(1); c.write(std::string_view(encrypted).substr(0,prefix)); c.run();
    check(!c.server->sock.is_open() && c.dispatched==0 && c.server->encrypted_candidates.empty(),
          "an encrypted prefix or header cannot disarm the unbound message deadline");
  }
  {
    connection c(2); c.write(std::string_view(encrypted).substr(0,sizeof(encrypted_rtsp_header_t))); c.run();
    check(!c.server->sock.is_open() && c.dispatched==0,
          "a bound encrypted header without its body still expires");
  }
  for (int mode : {0,3}) {
    connection c(mode); c.write("ANNOUNCE * RTSP/1.0\r\nCSeq: 1\r\nContent-length: 20\r\n\r\nx"); c.run();
    check(!c.server->sock.is_open() && c.dispatched==0,
          "an incomplete plaintext body expires on both unbound and bound paths");
  }
  {
    connection c(3); c.write("OPT");
    boost::asio::steady_timer fragment(c.io,40ms);
    fragment.async_wait([&](const boost::system::error_code &ec){if(!ec)c.write("IONS");});
    // Inspect the absolute deadline after a second fragment, before it expires.
    const auto original=c.server->idle_timer.expiry();
    c.io.run_for(70ms);
    check(c.server->idle_timer.expiry()==original,
          "partial plaintext headers do not renew the first-message deadline");
    c.io.restart(); c.run();
    check(!c.server->sock.is_open(), "a fragmented first header ultimately expires");
  }
  for (int mode : {0,1,2,3}) {
    connection c(mode);
    c.write(mode==1 || mode==2 ? encrypted : "OPTIONS * RTSP/1.0\r\nCSeq: 1\r\n\r\n"); c.run();
    check(c.dispatched==1 && c.server->sock.is_open() && c.server->first_message_done,
          "a complete first message retires the timer and remains connected");
  }
  {
    connection c(3); c.write("malformed\r\n\r\n"); c.run();
    check(!c.server->sock.is_open() && c.dispatched==0,
          "an invalid complete message still closes without dispatch");
  }
  std::cout<<checks<<" checks; "<<failures<<" failures\n";
  return failures!=0;
}
'''
code = code.replace('@@ROUTE@@', block(policy, '  initial_route_e choose_initial_route('))
code = code.replace('@@HEADER@@', block(source, '  struct encrypted_rtsp_header_t'))
socket = block(source, '  class socket_t: public std::enable_shared_from_this<socket_t>')
assert socket.count('std::chrono::seconds(10)') == 1
socket = socket.replace('std::chrono::seconds(10)', 'std::chrono::milliseconds(100)')
code = code.replace('@@SOCKET@@', socket)
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code)
    parser = ROOT / 'third-party/moonlight-common-c/src'
    flags = ['-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    subprocess.run(['cc', *flags, '-I', str(parser), '-c', str(parser / 'RtspParser.c'), '-o', str(work / 'parser.o')], check=True)
    subprocess.run(['g++', '-std=c++20', *flags, '-I', str(parser), str(work / 'test.cpp'), str(work / 'parser.o'),
                    '-pthread', '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'), timeout=15).returncode)
