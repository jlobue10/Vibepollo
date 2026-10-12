#!/usr/bin/env python3
"""Compile saveApp's plain_local_path lambda for Windows and POSIX; text-gate that
saveApp refuses non-plain-local output/working-dir paths and embedded NULs in command strings
before migrating the app. --baseline reads HEAD.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
    return (ROOT / path).read_text(encoding='utf-8')


def block(source, marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


confighttp = read('src/confighttp.cpp')
save = block(confighttp, 'void saveApp(')
failures = 0


def check(ok, name):
    global failures
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok


check('must be a plain local path' in save and save.index('must be a plain local path') < save.index('proc::migrate_apps('),
      'saveApp refuses non-plain-local output/working-dir paths before migrating the app')
check('contains an embedded NUL' in save and save.index('contains an embedded NUL') < save.index('proc::migrate_apps('),
      'saveApp refuses embedded NULs in command strings before migrating the app')

lam = save[save.index('const auto plain_local_path = [](const std::string &v)'):]
open_brace = lam.index('{')
depth, end = 1, open_brace + 1
while depth:
    depth += (lam[end] == '{') - (lam[end] == '}')
    end += 1
lam = lam[:end] + ';'
code = r"""
#include <algorithm>
#include <cctype>
#include <iostream>
#include <string>
#include <string_view>
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
""" + lam + r"""
  for (const char *good : {"", "null", "C:\\logs\\a.log", "logs\\a.log", "$(APPDATA)\\x", "C:\\Program Files\\Game\\out.txt"}) {
    check(plain_local_path(good), good[0] ? good : "(empty)");
  }
  const std::string nul("a\0b", 3);
  check(!plain_local_path(nul), "embedded NUL is rejected on every platform");
#ifdef _WIN32
  for (const std::string bad : {std::string("\\\\srv\\share\\a"), std::string("//srv/share"), std::string("\\\\.\\pipe\\x"), std::string("\\\\?\\C:\\a"),
                                std::string("C:\\a.log:stream"), std::string("C:\\logs\\NUL"), std::string("C:\\logs\\com1.txt"), std::string("9:\\x"), nul}) {
    check(!plain_local_path(bad), ("rejected: " + (bad == nul ? std::string("a<NUL>b") : bad)).c_str());
  }
#else
  for (const char *good : {"/home/games/Title: Subtitle", "/tmp/logs/COM1.txt", "/tmp/NUL", "/tmp/CON",
                           "logs/game:output.txt", "9:filename", "//srv/local/path"}) {
    check(plain_local_path(good), good);
  }
#endif
  std::cout << failures << " failures\n";
  return failures != 0;
}
"""
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code, encoding='utf-8')
    for platform_flags in (['-D_WIN32'], []):
        print('Windows' if platform_flags else 'POSIX', flush=True)
        subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        *platform_flags, str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
        failures += subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode != 0
sys.exit(1 if failures else 0)
