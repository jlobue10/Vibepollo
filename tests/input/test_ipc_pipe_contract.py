#!/usr/bin/env python3
"""Execute production pipe framing and overlapped completion with fake transports.

No Windows services or IPC endpoints are opened. --baseline reads HEAD for a
before/after reproduction. Real OS scheduling still needs Windows integration.
"""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
    return (ROOT / path).read_text()


def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


source = read('src/platform/windows/ipc/pipes.cpp')
header = read('src/platform/windows/ipc/pipes.h')
prelude = r'''
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <deque>
#include <iostream>
#include <memory>
#include <span>
#include <utility>
#include <vector>
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) Log{}
using DWORD = uint32_t;
using BOOL = bool;
constexpr BOOL TRUE = true, FALSE = false;
constexpr DWORD WAIT_OBJECT_0=0, WAIT_TIMEOUT=258, WAIT_FAILED=0xffffffff;
constexpr DWORD ERROR_IO_PENDING=997, ERROR_OPERATION_ABORTED=995, ERROR_BROKEN_PIPE=109,
  ERROR_PIPE_NOT_CONNECTED=233, ERROR_NO_DATA=232, ERROR_NOT_FOUND=1168;
struct io_context { bool is_valid(){return true;} void *get(){return this;} void *event(){return this;} };
namespace fake {
  DWORD wait=WAIT_OBJECT_0, completion_error=0, count=7, error=0;
  int cancels=0, completions=0; bool waited=false;
  void reset(DWORD w, DWORD e=0) { wait=w;completion_error=e;count=7;error=0;cancels=0;completions=0;waited=false; }
}
DWORD WaitForSingleObject(void *, int){return fake::wait;}
BOOL CancelIoEx(void *, void *) { ++fake::cancels;fake::error=ERROR_NOT_FOUND;return FALSE; }
DWORD GetLastError(){return fake::error;}
BOOL GetOverlappedResult(void *, void *, DWORD *n, BOOL wait) {
  ++fake::completions;fake::waited=wait;fake::error=fake::completion_error;
  *n=fake::completion_error ? 0 : fake::count;return fake::completion_error==0;
}
BOOL WriteFile(void *, const void *, DWORD, DWORD *, void *) {fake::error=ERROR_IO_PENDING;return FALSE;}
namespace platf::dxgi {
'''
classes = header[header.index('  enum class PipeResult'):header.index('  class AsyncNamedPipe {')]
framing = source[source.index('  FramedPipe::FramedPipe('):source.index('  SelfHealingPipe::SelfHealingPipe(')]
win = r'''
class WinPipe {
public:
  struct Handle {void *get(){return this;} explicit operator bool()const{return true;}} _pipe;
  std::atomic<bool> _connected{true};
  bool send(std::span<const uint8_t>, int);
  bool handle_send_error(io_context &, int, DWORD &);
  bool handle_pending_send_operation(io_context &, int, DWORD &);
  PipeResult handle_pending_receive_operation(io_context &, int, std::span<uint8_t>, size_t &);
};
'''
win += '\n'.join(function(source, signature) for signature in [
    'bool WinPipe::send(', 'bool WinPipe::handle_send_error(',
    'bool WinPipe::handle_pending_send_operation(',
    'PipeResult WinPipe::handle_pending_receive_operation('])
tests = r'''
class RawPipe: public INamedPipe {
public:
  std::deque<std::vector<uint8_t>> incoming;
  std::vector<uint8_t> sent;
  bool connected=true;int reads=0;
  bool send(std::span<const uint8_t> b,int) override {sent.assign(b.begin(),b.end());return true;}
  PipeResult receive(std::span<uint8_t> dst,size_t &n,int) override {
    ++reads;n=0;if(!connected)return PipeResult::Disconnected;
    if(incoming.empty())return PipeResult::Timeout;
    auto &b=incoming.front();n=std::min(b.size(),dst.size());std::copy_n(b.begin(),n,dst.begin());
    b.erase(b.begin(),b.begin()+n);if(b.empty())incoming.pop_front();return PipeResult::Success;
  }
  PipeResult receive_latest(std::span<uint8_t> b,size_t &n,int t) override{return receive(b,n,t);}
  void wait_for_client_connection(int) override{}
  void disconnect() override{connected=false;}
  bool is_connected() override{return connected;}
};
std::vector<uint8_t> frame(std::vector<uint8_t> body) {
  uint32_t n=body.size();std::vector<uint8_t> b(4);std::memcpy(b.data(),&n,4);
  b.insert(b.end(),body.begin(),body.end());return b;
}
} // namespace platf::dxgi
int main() {
  using namespace platf::dxgi;
  using enum PipeResult;
  int failures=0;
  auto check=[&](bool ok,const char *name){std::cout<<(ok?"PASS ":"FAIL ")<<name<<'\n';failures+=!ok;};
  std::array<uint8_t,16> dst{};size_t n=0;
  {
    auto inner=std::make_unique<RawPipe>();auto *raw=inner.get();FramedPipe p(std::move(inner));
    auto b=frame({1,2,3});raw->incoming.push_back({b.begin(),b.begin()+2});
    check(p.receive(dst,n,100)==Timeout&&n==0,"fragmented header waits without delivering data");
    raw->incoming.push_back({b.begin()+2,b.end()-1});
    check(p.receive(dst,n,100)==Timeout&&n==0,"fragmented payload waits without delivering data");
    raw->incoming.push_back({b.back()});
    check(p.receive(dst,n,100)==Success&&n==3&&dst[0]==1&&dst[2]==3,"fragmented frame is delivered exactly once");
    auto joined=frame({4});auto next=frame({5,6});joined.insert(joined.end(),next.begin(),next.end());raw->incoming.push_back(joined);
    check(p.receive_latest(dst,n,100)==Success&&n==2&&dst[0]==5&&dst[1]==6,"coalesced frames yield the latest complete payload");
    raw->incoming.push_back(std::vector<uint8_t>(65536,255));
    check(p.receive(dst,n,100)==Timeout&&n==0,"invalid header bytes are discarded without a false payload");
    p.disconnect();raw->connected=true;
    auto partial=frame({7});raw->incoming.push_back({partial.begin(),partial.begin()+3});p.receive(dst,n,0);
    p.disconnect();raw->connected=true;raw->incoming.push_back(frame({10}));
    check(p.receive(dst,n,100)==Success&&n==1&&dst[0]==10,"disconnect clears a partial frame before reuse");
    check(p.send(std::array<uint8_t,2>{3,4},100)&&raw->sent==frame({3,4}),"outgoing frames use the length prefix");
  }
  {
    auto inner=std::make_unique<RawPipe>();auto *raw=inner.get();FramedPipe p(std::move(inner));
    // Real AsyncNamedPipe uses a 64 KiB destination. No payload is needed to
    // establish that a 65537-byte frame can never fit that destination.
    std::array<uint8_t,65536> actual_dst{};uint32_t len=65537;std::vector<uint8_t> hdr(4);std::memcpy(hdr.data(),&len,4);
    raw->incoming.push_back(hdr);raw->incoming.push_back(std::vector<uint8_t>(65536,1));
    check(p.receive(actual_dst,n,100)==Error&&n==0&&!raw->connected&&raw->reads==1,
      "oversized frame header fails immediately instead of retaining following traffic forever");
    check(p.receive(actual_dst,n,0)==Disconnected&&n==0,"oversized frame disconnects the unusable stream");
  }
  {
    WinPipe p;io_context ctx;
    fake::reset(WAIT_TIMEOUT);n=0;
    check(p.handle_pending_receive_operation(ctx,0,dst,n)==Success&&n==7&&fake::cancels==1&&fake::waited,
      "read completion winning cancellation returns its consumed bytes");
    fake::reset(WAIT_TIMEOUT,ERROR_OPERATION_ABORTED);n=0;
    check(p.handle_pending_receive_operation(ctx,0,dst,n)==Timeout&&n==0&&fake::waited,
      "cancelled timed-out read remains a timeout");
    for(auto error:{ERROR_BROKEN_PIPE,ERROR_PIPE_NOT_CONNECTED,ERROR_NO_DATA}) {
      p._connected=true;fake::reset(WAIT_TIMEOUT,error);n=0;
      check(p.handle_pending_receive_operation(ctx,0,dst,n)==BrokenPipe&&!p._connected,
        "pipe loss during timeout drain is surfaced as broken");
    }
    p._connected=true;fake::reset(WAIT_OBJECT_0);n=0;
    check(p.handle_pending_receive_operation(ctx,100,dst,n)==Success&&n==7&&fake::cancels==0&&!fake::waited,
      "signalled read completion uses its actual byte count");
    fake::reset(WAIT_FAILED,ERROR_OPERATION_ABORTED);
    check(p.handle_pending_receive_operation(ctx,0,dst,n)==Error&&fake::cancels==1&&fake::completions==1&&fake::waited,
      "failed wait drains pending I/O before its context leaves scope");
    fake::reset(WAIT_TIMEOUT);DWORD written=0;
    check(p.handle_pending_send_operation(ctx,0,written)&&written==7&&fake::waited,
      "write completion winning cancellation is reported as delivered");
    fake::reset(WAIT_TIMEOUT,ERROR_OPERATION_ABORTED);written=0;
    check(!p.handle_pending_send_operation(ctx,0,written)&&fake::waited,"cancelled write is not reported as delivered");
    fake::reset(WAIT_OBJECT_0);fake::count=3;
    check(!p.send(dst,100),"short overlapped write is rejected by the same byte-count check as synchronous writes");
    fake::reset(WAIT_TIMEOUT);fake::count=dst.size();
    check(p.send(dst,0),"full late overlapped write is accepted");
  }
  return failures?1:0;
}
'''
with tempfile.TemporaryDirectory(prefix='ipc-contract-') as temp:
    cpp, binary = Path(temp)/'test.cpp', Path(temp)/'test'
    cpp.write_text(prelude + classes + framing + win + tests)
    subprocess.run([*shlex.split(os.environ.get('CXX', 'c++')), '-std=c++20', '-Wall', '-Wextra',
                    '-Wno-unused-parameter', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(cpp), '-o', str(binary)], check=True)
    env = dict(os.environ)
    env.setdefault('ASAN_OPTIONS', 'detect_leaks=0')
    sys.exit(subprocess.run([str(binary)], env=env, timeout=30).returncode)
