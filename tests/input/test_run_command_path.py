#!/usr/bin/env python3
"""Compile the production program resolver and child-PATH helper from run_command() against a
scripted SearchPathW and a map-backed environment. The host's own PATH used to be prepended to
(under a process-wide mutex) so CreateProcess would find binaries beside the launch target; the
program is now pinned by SearchPathW (working directory first, then the host order) and passed
as lpApplicationName, and only the child's PATH carries the prefix. --baseline reads HEAD.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATHS = ['src/platform/windows/misc.cpp']


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
    return (ROOT / path).read_text(encoding='utf-8')


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


misc = read(PATHS[0])
run_command = function(misc, '  bp::child run_command(')
failures = 0


def check(ok, name):
    global failures
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok


check('this_process::wenv()' not in run_command and 'host_path_mutex' not in run_command,
      'run_command() no longer edits the host PATH or serialises launches behind a mutex')
check(run_command.count('program.empty() ? nullptr : program.c_str()') == 2,
      'both CreateProcess calls receive the resolved program as lpApplicationName')
check(run_command.count('prepend_child_path(cloned_env, start_dir)') == 2 and
      run_command.index('prepend_child_path(cloned_env, start_dir)') > run_command.index('merge_user_environment_block(cloned_env, user_token)'),
      'the working directory is prepended to the child PATH after the user block is merged')

PRELUDE = r"""
#include <algorithm>
#include <array>
#include <cstring>
#include <initializer_list>
#include <iostream>
#include <map>
#include <string>
#include <string_view>
#include <vector>
typedef unsigned long DWORD;
static std::map<std::wstring, std::wstring> hits;   // "<lpPath>|<name>" -> full path
static std::vector<std::wstring> lookups;
static DWORD SearchPathW(const wchar_t *path, const wchar_t *name, const wchar_t *ext, DWORD n, wchar_t *buf, wchar_t **part) {
  (void)ext; (void)part;
  const std::wstring key = std::wstring(path ? path : L"<null>") + L"|" + name;
  lookups.push_back(key);
  auto it = hits.find(key);
  if (it == hits.end()) return 0;
  if (it->second.size() >= n) return (DWORD) it->second.size() + 1;
  std::wcsncpy(buf, it->second.c_str(), n);
  return (DWORD) it->second.size();
}
namespace boost { static bool iequals(const std::string &a, const std::string &b) {
  if (a.size() != b.size()) return false;
  for (size_t i = 0; i < a.size(); ++i) if (std::tolower((unsigned char) a[i]) != std::tolower((unsigned char) b[i])) return false;
  return true; } }
namespace utf_utils { static std::string to_utf8(const std::wstring &s) { return std::string(s.begin(), s.end()); } }
namespace bp {
  struct entry_t {
    std::string name, value;
    std::string get_name() const { return name; }
    std::string to_string() const { return value; }
    entry_t &operator=(const std::string &v) { value = v; return *this; }
  };
  struct environment {
    std::vector<entry_t> items;
    auto begin() { return items.begin(); }
    auto end() { return items.end(); }
    entry_t &operator[](const std::string &name) {
      for (auto &e : items) if (e.name == name) return e;
      items.push_back({name, ""}); return items.back();
    }
  };
}
"""
MAIN = r"""
int main() {
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  const std::wstring dir = L"C:\\Games\\Foo";
  hits[L"C:\\Games\\Foo|game.exe"] = L"C:\\Games\\Foo\\game.exe";
  hits[L"<null>|cmd.exe"] = L"C:\\Windows\\System32\\cmd.exe";
  hits[L"<null>|tool"] = L"C:\\Tools\\tool.exe";
  check(resolve_program_path(L"game.exe --fullscreen", dir) == L"C:\\Games\\Foo\\game.exe", "a bare program is found in the working directory first");
  check(resolve_program_path(L"\"game.exe\" --fullscreen", dir) == L"C:\\Games\\Foo\\game.exe", "a quoted bare program resolves the same way");
  lookups.clear();
  check(resolve_program_path(L"tool arg", dir) == L"C:\\Tools\\tool.exe" && lookups.size() == 2 && lookups[0].rfind(dir + L"|", 0) == 0,
        "a program missing from the working directory falls back to the host search order");
  check(resolve_program_path(L"cmd.exe /c start \"\" \"C:\\x\\y.lnk\"", dir) == L"C:\\Windows\\System32\\cmd.exe", "the cmd.exe trampoline resolves on the host order");
  check(resolve_program_path(L"\"C:\\Program Files\\x\\a.exe\" args", dir).empty(), "a quoted qualified path is left to CreateProcess (lpApplicationName stays null)");
  check(resolve_program_path(L"C:\\Program Files\\x\\a.exe args", dir).empty(), "an unquoted qualified path with spaces keeps the old lookup");
  check(resolve_program_path(L"..\\bin\\a.exe", dir).empty() && resolve_program_path(L"bin/a.exe", dir).empty(), "relative paths with separators are not re-resolved");
  check(resolve_program_path(L"nothere.exe", dir).empty(), "an unknown program falls back to CreateProcess's own lookup");
  check(resolve_program_path(L"", dir).empty(), "an empty command line resolves nothing");
  lookups.clear();
  hits[L"<null>|game.exe"] = L"C:\\Elsewhere\\game.exe";
  check(resolve_program_path(L"game.exe", L"") == L"C:\\Elsewhere\\game.exe" && lookups.size() == 1 && lookups[0] == L"<null>|game.exe",
        "without a working directory only the host order is searched");
  bp::environment env; env.items = {{"Path", "C:\\Windows"}, {"OTHER", "1"}};
  prepend_child_path(env, dir);
  check(env.items.size() == 2 && env.items[0].name == "Path" && env.items[0].value == "C:\\Games\\Foo;C:\\Windows", "the child PATH starts with the working directory (existing key kept)");
  bp::environment empty_env;
  prepend_child_path(empty_env, dir);
  check(empty_env.items.size() == 1 && empty_env.items[0].value == "C:\\Games\\Foo", "a missing PATH is created from the working directory");
  prepend_child_path(env, L"");
  check(env.items[0].value == "C:\\Games\\Foo;C:\\Windows", "an empty working directory leaves the child PATH alone");
  std::cout << failures << " failures\n";
  return failures != 0;
}
"""
code = PRELUDE + function(misc, '  static std::wstring_view first_token(') + '\n' + function(misc, '  static std::wstring resolve_program_path(') + '\n' + function(misc, '  static void prepend_child_path(') + MAIN
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code, encoding='utf-8')
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    failures += subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode != 0
sys.exit(1 if failures else 0)
