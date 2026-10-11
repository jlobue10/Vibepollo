#!/usr/bin/env python3
"""Compile the production Windows environment-block builder (create_environment_block) and the
launch-argument validator against stand-ins, and check that a NUL cannot start extra entries.

A paired client's clientName/surroundParams are copied into the launched process's environment; the
block is NAME=VALUE pairs separated by NULs, so a %00 in the query used to add attacker-chosen
entries (JAVA_TOOL_OPTIONS, NODE_OPTIONS, ...) to every command the host launched for the session.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MISC = ROOT / 'src/platform/windows/misc.cpp'
POLICY = ROOT / 'src/http_pairing_policy.cpp'


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


PRELUDE = r'''
#include <algorithm>
#include <cctype>
#include <cstring>
#include <iostream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) Log{}
namespace bp {
  struct entry_t { std::string name, value; std::string get_name() const { return name; } std::string to_string() const { return value; } };
  using environment = std::vector<entry_t>;
}
namespace utf_utils { std::wstring from_utf8(const std::string &s) { return std::wstring(s.begin(), s.end()); } }
namespace platf {
'''

TESTS = r'''
}  // namespace platf
namespace confighttp::pairing_policy {
'''

MAIN = r'''
}
static std::vector<std::wstring> entries_of(const std::wstring &block) {
  std::vector<std::wstring> out; std::wstring current;
  for (wchar_t c : block) { if (c == L'\0') { if (current.empty()) break; out.push_back(current); current.clear(); } else current += c; }
  return out;
}
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  bp::environment env {{"PATH", "C:\\Windows"}, {"APOLLO_CLIENT_NAME", std::string("Phone\0JAVA_TOOL_OPTIONS=-javaagent:x", 36)}, {"SAFE", "1"}};
  const auto entries = entries_of(platf::create_environment_block(env));
  int client = 0, injected = 0;
  for (const auto &e : entries) { client += e.rfind(L"APOLLO_CLIENT_NAME=", 0) == 0; injected += e.rfind(L"JAVA_TOOL_OPTIONS=", 0) == 0; }
  check(injected == 0, "a NUL inside a value cannot start a new environment entry");
  check(client == 0 && entries.size() == 2, "the tainted entry is dropped rather than split");
  bp::environment clean {{"A", "1"}, {"B", "two"}};
  check(entries_of(platf::create_environment_block(clean)) == std::vector<std::wstring> {L"A=1", L"B=two"}, "clean entries are laid out as NAME=VALUE");
  using confighttp::pairing_policy::launch_argument_printable;
  check(launch_argument_printable("Galaxy XR") && launch_argument_printable("48kHz 7.1 \u00e9"), "ordinary names pass");
  check(!launch_argument_printable(std::string("a\0b", 3)) && !launch_argument_printable("a\nb") && !launch_argument_printable("a\x7f"), "control characters are rejected");
  check(launch_argument_printable(""), "an empty value is acceptable");
  return failures ? 1 : 0;
}
'''


def main():
    misc = MISC.read_text(encoding='utf-8')
    policy = POLICY.read_text(encoding='utf-8')
    try:
        body = function(misc, 'void append_string_to_environment_block(') + '\n' + function(misc, 'std::wstring create_environment_block(')
        gate = function(policy, 'bool launch_argument_printable(')
    except ValueError as error:
        print('FAIL: production helper missing:', error)
        return 1
    with tempfile.TemporaryDirectory(prefix='env-block-nul-') as temp:
        cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
        cpp.write_text(PRELUDE + body + TESTS + gate + MAIN, encoding='utf-8')
        subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-Wno-unused-parameter', str(cpp), '-o', str(binary)], check=True)
        return subprocess.run([str(binary)]).returncode


if __name__ == '__main__':
    sys.exit(main())
