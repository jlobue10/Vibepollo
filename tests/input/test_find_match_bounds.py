#!/usr/bin/env python3
"""Compile the production find_match (src/process.cpp, the `$(...)` scanner of parse_env_val) and
run it under ASan on an exact-size heap buffer holding an unbalanced "$(abc": the previous loop
dereferenced one element past the end before throwing. --baseline reads HEAD.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = 'src/process.cpp'


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
    return (ROOT / path).read_text(encoding='utf-8')


def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


body = function(read(PATH), 'std::string_view::iterator find_match(std::string_view::iterator begin, std::string_view::iterator end)')
code = r"""
#include <cstring>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string_view>
namespace proc {
""" + body + r"""
}
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  {
    const char text[] = "$(abc)rest";
    std::string_view view(text, sizeof(text) - 1);
    auto it = proc::find_match(view.begin() + 1, view.end());
    check(it != view.end() && *it == ')' && it - view.begin() == 5, "a balanced group returns its closing bracket");
  }
  {
    const char text[] = "$(a(b)c)x";
    std::string_view view(text, sizeof(text) - 1);
    auto it = proc::find_match(view.begin() + 1, view.end());
    check(it - view.begin() == 7, "nested brackets are matched");
  }
  {
    // Exact-size heap buffer, no terminator: the old loop read one past the end here.
    const std::size_t n = 5;
    std::unique_ptr<char[]> heap(new char[n]);
    std::memcpy(heap.get(), "$(abc", n);
    std::string_view view(heap.get(), n);
    bool threw = false;
    try {
      (void) proc::find_match(view.begin() + 1, view.end());
    } catch (const std::out_of_range &) {
      threw = true;
    }
    check(threw, "an unbalanced group throws without reading past the end");
  }
  return failures ? 1 : 0;
}
"""
with tempfile.TemporaryDirectory(prefix='find-match-') as temp:
    cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
    cpp.write_text(code, encoding='utf-8')
    subprocess.run(['c++', '-std=c++20', '-Wall', '-fsanitize=address,undefined', str(cpp), '-o', str(binary)], check=True)
    sys.exit(subprocess.run([str(binary)]).returncode)
