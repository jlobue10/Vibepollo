#!/usr/bin/env python3
"""Compile the production RequestAuthPolicy::extract_cookie and check exact cookie-name matching.

The extractor used to be a substring search, so a cookie whose name merely ended in
`__Host-apollo_session` (settable by a sibling host under a shared parent domain) was taken as ours.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src/http_auth_request_policy.cpp'


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


PRELUDE = r'''
#include <functional>
#include <iostream>
#include <map>
#include <string>
#include <string_view>
namespace SimpleWeb { using CaseInsensitiveMultimap = std::multimap<std::string, std::string>; }
namespace confighttp::policy {
  struct dependencies_t {
    std::function<std::string(const std::string &)> cookie_unescape = [](const std::string &s) { return s; };
  };
  class RequestAuthPolicy {
  public:
    std::string extract_cookie(const SimpleWeb::CaseInsensitiveMultimap &headers, std::string_view name) const;
    dependencies_t _dependencies;
  };
'''

TESTS = r'''
}  // namespace confighttp::policy
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  confighttp::policy::RequestAuthPolicy policy;
  auto extract = [&](const std::string &cookie) {
    SimpleWeb::CaseInsensitiveMultimap headers;
    headers.emplace("Cookie", cookie);
    return policy.extract_cookie(headers, "__Host-apollo_session");
  };
  check(extract("a__Host-apollo_session=evil; __Host-apollo_session=good") == "good", "a longer-named cookie listed first is not taken");
  check(extract("__Host-apollo_session=good; a__Host-apollo_session=evil") == "good", "the exact name wins when listed first");
  check(extract("x=1;  __Host-apollo_session=good") == "good", "leading whitespace before the pair is ignored");
  check(extract("__Host-apollo_sessionx=bad") == "", "a longer name with our name as prefix is not taken");
  check(extract("other=1") == "" && policy.extract_cookie({}, "__Host-apollo_session") == "", "a missing cookie yields nothing");
  check(extract("__Host-apollo_session=") == "", "an empty value is empty");
  return failures ? 1 : 0;
}
'''


def main():
    body = function(SRC.read_text(encoding='utf-8'), 'std::string RequestAuthPolicy::extract_cookie(')
    with tempfile.TemporaryDirectory(prefix='cookie-extract-') as temp:
        cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
        cpp.write_text(PRELUDE + body + TESTS, encoding='utf-8')
        subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', str(cpp), '-o', str(binary)], check=True)
        return subprocess.run([str(binary)]).returncode


if __name__ == '__main__':
    sys.exit(main())
