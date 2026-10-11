#!/usr/bin/env python3
"""Compile the production config-file parser (endline .. parse_config in src/config.cpp) and check
that a '#' inside a value is kept: only a line whose first non-blank character is '#' is a comment.

Stripping '#' anywhere truncated JSON-valued options (a prep command "cmd /c echo #1", a Playnite
title such as "#DRIVE"); the truncated JSON then threw out of apply_config, which parse() did not
catch (startup crash loop), and hot-apply caught only after the runtime config was reset.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'src/config.cpp'

PRELUDE = r'''
#include <algorithm>
#include <functional>
#include <iostream>
#include <iterator>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <tuple>
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) Log{}
#define KITTY_WHILE_LOOP(x, y, z) { x; while (y) z }
#define TUPLE_2D(a, b, expr) decltype(expr) a##_##b = expr; auto &a = std::get<0>(a##_##b); auto &b = std::get<1>(a##_##b)
namespace config {
  // Key aliasing is not under test: identity stand-in.
  std::string normalize_config_key(std::string key) { return key; }
'''

MAIN = r'''
}  // namespace config
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  const std::string text =
    "# a full-line comment\n"
    "global_prep_cmd = [{\"do\":\"cmd /c echo #1\",\"undo\":\"\",\"elevated\":false}]\n"
    "   # an indented comment line\n"
    "playnite_exclude_games = #DRIVE, Other\n"
    "trailing = value # not a comment any more\n"
    "list = [\n"
    "  # a comment line inside a bracketed value\n"
    "  a,\n"
    "  b\n"
    "]\n"
    "plain = 1\n";
  auto vars = config::parse_config(text);
  check(vars.count("# a full-line comment") == 0 && vars.count("# an indented comment line") == 0, "comment lines are skipped");
  check(vars["global_prep_cmd"] == "[{\"do\":\"cmd /c echo #1\",\"undo\":\"\",\"elevated\":false}]", "a '#' inside a JSON value survives");
  check(vars["playnite_exclude_games"] == "#DRIVE, Other", "a value starting with '#' survives");
  check(vars["trailing"] == "value # not a comment any more", "only a line-leading '#' is a comment");
  check(vars["list"].find('#') == std::string::npos && vars["list"].find("a,") != std::string::npos && vars["list"].find('b') != std::string::npos,
        "comment lines inside a bracketed value are dropped, the entries kept");
  check(vars["plain"] == "1" && vars.size() == 5, "ordinary options are unchanged");
  return failures ? 1 : 0;
}
'''


def main():
    source = CONFIG.read_text(encoding='utf-8')
    try:
        start = source.index('  bool endline(char ch)')
        end = source.index('  void string_f(')
    except ValueError as error:
        print('FAIL: parser anchors missing:', error)
        return 1
    with tempfile.TemporaryDirectory(prefix='config-hash-value-') as temp:
        cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
        cpp.write_text(PRELUDE + source[start:end] + MAIN, encoding='utf-8')
        subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-Wno-unused-function', str(cpp), '-o', str(binary)], check=True)
        return subprocess.run([str(binary)]).returncode


if __name__ == '__main__':
    sys.exit(main())
