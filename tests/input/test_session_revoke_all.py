#!/usr/bin/env python3
"""Compile the production SessionTokenManager::revoke_all against a stub manager.

Changing the web UI password used to clear a dead global string; every session cookie, refresh
token and API token issued before the change kept working.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src/http_auth.cpp'


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


PRELUDE = r'''
#include <cstddef>
#include <iostream>
#include <mutex>
#include <string>
#include <unordered_map>
namespace confighttp {
  struct SessionToken { std::string refresh_token_hash; };
  class SessionTokenManager {
  public:
    std::size_t revoke_all();
    void save_session_tokens() const { ++saves; }
    mutable std::mutex _mutex;
    std::unordered_map<std::string, SessionToken> _session_tokens;
    std::unordered_map<std::string, std::string> _refresh_index;
    mutable bool _dirty = false;
    mutable int saves = 0;
  };
'''

TESTS = r'''
}  // namespace confighttp
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  confighttp::SessionTokenManager manager;
  for (int i = 0; i < 3; ++i) {
    const auto id = std::to_string(i);
    manager._session_tokens.emplace("s" + id, confighttp::SessionToken {"r" + id});
    manager._refresh_index.emplace("r" + id, "s" + id);
  }
  check(manager.revoke_all() == 3, "revoke_all reports the removed sessions");
  check(manager._session_tokens.empty() && manager._refresh_index.empty(), "both token maps are empty afterwards");
  check(manager.saves == 1, "the emptied state is persisted");
  check(manager.revoke_all() == 0, "a second call finds nothing");
  return failures ? 1 : 0;
}
'''


def main():
    try:
        body = function(SRC.read_text(encoding='utf-8'), 'std::size_t SessionTokenManager::revoke_all(')
    except ValueError:
        print('FAIL: SessionTokenManager::revoke_all is missing')
        return 1
    with tempfile.TemporaryDirectory(prefix='session-revoke-all-') as temp:
        cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
        cpp.write_text(PRELUDE + body + TESTS, encoding='utf-8')
        subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', str(cpp), '-o', str(binary)], check=True)
        return subprocess.run([str(binary)]).returncode


if __name__ == '__main__':
    sys.exit(main())
