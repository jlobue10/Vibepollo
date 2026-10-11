#!/usr/bin/env python3
"""Execute the production anonymous-pipe handshake client (AnonymousPipeFactory::handshake_client in
src/platform/windows/ipc/pipes.cpp) against a peer that fills the 40-wchar pipe-name field with no
terminator, under ASan.

The field is peer-supplied (a squatter on the Playnite or display-helper control pipe can send it);
the unbounded std::wstring constructor read past it on the SYSTEM host. --baseline reads HEAD.
No Windows pipes are opened.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPES = 'src/platform/windows/ipc/pipes.cpp'


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
    return (ROOT / path).read_text(encoding='utf-8')


def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


PRELUDE = r"""
#include <cstdint>
#include <cstring>
#include <cwchar>
#include <iostream>
#include <memory>
#include <string>
#include <vector>
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) Log{}
namespace platf::dxgi {
  struct INamedPipe { virtual ~INamedPipe() = default; virtual void disconnect() { ++disconnects; } static inline int disconnects = 0; };
  struct AnonConnectMsg { wchar_t pipe_name[40]; };
  struct PrefetchedPipe: INamedPipe { PrefetchedPipe(std::unique_ptr<INamedPipe>, std::vector<uint8_t>) {} };
  std::string wide_to_utf8(const std::wstring &w) { return std::string(w.begin(), w.end()); }
  // The peer's message: filled by the fake receive below.
  static std::wstring peer_name; static bool peer_terminated = true;
  static std::vector<std::string> connected_names;
  class AnonymousPipeFactory {
  public:
    enum class HandshakeMessageResult { Failed, Inline, Message };
    std::unique_ptr<INamedPipe> handshake_client(std::unique_ptr<INamedPipe> pipe);
    HandshakeMessageResult receive_handshake_message(std::unique_ptr<INamedPipe> &pipe, AnonConnectMsg &msg, std::vector<uint8_t> &prefetched) const;
    bool send_handshake_ack(std::unique_ptr<INamedPipe> &pipe) const { (void) pipe; return true; }
    std::unique_ptr<INamedPipe> connect_to_data_pipe(const std::string &name) { connected_names.push_back(name); return std::make_unique<INamedPipe>(); }
  };
  AnonymousPipeFactory::HandshakeMessageResult AnonymousPipeFactory::receive_handshake_message(std::unique_ptr<INamedPipe> &, AnonConnectMsg &msg, std::vector<uint8_t> &prefetched) const {
    prefetched.clear();
    std::memset(msg.pipe_name, 0, sizeof(msg.pipe_name));
    if (peer_terminated) {
      std::wcsncpy(msg.pipe_name, peer_name.c_str(), 39);
    } else {
      for (auto &c : msg.pipe_name) c = L'A';  // 40 non-terminated characters
    }
    return HandshakeMessageResult::Message;
  }
"""

MAIN = r"""
}  // namespace platf::dxgi
int main() {
  using namespace platf::dxgi;
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  AnonymousPipeFactory factory;
  peer_terminated = false;
  auto out = factory.handshake_client(std::make_unique<INamedPipe>());
  check(out != nullptr && connected_names.size() == 1 && connected_names.back().size() == 40, "a non-terminated 40-wchar name is bounded to the field");
  peer_terminated = true; peer_name = L"0f3c6f2a-1b2c-4d5e-8f90-123456789abc"; connected_names.clear();
  out = factory.handshake_client(std::make_unique<INamedPipe>());
  check(connected_names.size() == 1 && connected_names.back() == "0f3c6f2a-1b2c-4d5e-8f90-123456789abc", "a terminated GUID name connects as sent");
  peer_name = L""; connected_names.clear(); INamedPipe::disconnects = 0;
  out = factory.handshake_client(std::make_unique<INamedPipe>());
  check(out != nullptr && connected_names.empty() && INamedPipe::disconnects == 0, "an empty name keeps the control pipe");
  peer_name = L"..\\..\\Device\\NamedPipe\\other"; connected_names.clear();
  out = factory.handshake_client(std::make_unique<INamedPipe>());
  check(out != nullptr && connected_names.empty(), "a path-like name is not opened");
  return failures ? 1 : 0;
}
"""


def main():
    source = read(PIPES)
    body = function(source, 'std::unique_ptr<INamedPipe> AnonymousPipeFactory::handshake_client(std::unique_ptr<INamedPipe> pipe)')
    with tempfile.TemporaryDirectory(prefix='anon-handshake-name-') as temp:
        cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
        cpp.write_text(PRELUDE + body + MAIN, encoding='utf-8')
        subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-fsanitize=address,undefined', str(cpp), '-o', str(binary)], check=True)
        return subprocess.run([str(binary)]).returncode


if __name__ == '__main__':
    sys.exit(main())
