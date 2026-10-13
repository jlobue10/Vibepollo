#!/usr/bin/env python3
"""Run the production apply_playnite() against scripted apps.json states: an unreadable,
malformed or array-less file leaves the catalogue untouched (no write), an absent file starts
an empty one, and a valid file gains the fullscreen entry. The apps-file mutex, the catalogue
writer, the Playnite sync and the file reader are boundary fakes; the file itself is real so
the existence check is the production one. --baseline reads HEAD.
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


source = read('src/config_playnite.cpp')
apply = block(source, '  void apply_playnite(')
code = r"""
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <optional>
#include <string>
#include <system_error>
#include <unordered_map>
#include <vector>
#include <nlohmann/json.hpp>
using namespace std::literals;
struct logger { template<class T> logger &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) logger()
#define _WIN32 1
namespace config {
  struct playnite_t { bool enabled = true; bool auto_sync = false; bool fullscreen_entry_enabled = true; };
  struct { std::string file_apps; } stream;
  playnite_t parse_playnite(std::unordered_map<std::string, std::string> &) { return {}; }
}
namespace confighttp {
  std::recursive_mutex &apps_file_mutex() { static std::recursive_mutex m; return m; }
  int writes = 0; nlohmann::json written;
  bool refresh_client_apps_cache(nlohmann::json &tree) { ++writes; written = tree; return true; }
}
namespace platf::playnite { void force_sync(bool) {} }
namespace file_handler {
  bool fail_read = false;
  std::optional<std::string> read_file_checked(const char *path) {
    if (fail_read) return std::nullopt;
    std::ifstream in(path, std::ios::binary);
    if (!in.is_open()) return std::nullopt;
    return std::string((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  }
  std::string read_file(const char *path) { return read_file_checked(path).value_or(""); }
}
namespace config {
  playnite_t playnite;
@@APPLY@@
}
static int checks = 0, failures = 0;
static void check(bool ok, const std::string &name) { ++checks; failures += !ok; if (!ok) std::cout << "FAIL " << name << '\n'; }
int main(int argc, char **argv) {
  const std::string path = std::string(argv[1]) + "/apps.json";
  config::stream.file_apps = path;
  std::unordered_map<std::string, std::string> vars;
  auto run = [&](const char *label, std::optional<std::string> content, bool unreadable) {
    std::filesystem::remove(path);
    if (content) { std::ofstream(path, std::ios::binary) << *content; }
    file_handler::fail_read = unreadable;
    confighttp::writes = 0; confighttp::written = nlohmann::json();
    config::apply_playnite(vars);
    file_handler::fail_read = false;
    return std::string(label);
  };
  // Untouched: unreadable, empty, malformed, array-less and non-object catalogues.
  check(confighttp::writes == 0 && (run("unreadable", std::string("{\"apps\":[{\"name\":\"A\"}]}"), true), confighttp::writes == 0),
        "an unreadable apps.json leaves the catalogue untouched");
  check((run("empty", std::string(""), false), confighttp::writes == 0), "an empty apps.json leaves the catalogue untouched");
  check((run("malformed", std::string("{"), false), confighttp::writes == 0), "a malformed apps.json leaves the catalogue untouched");
  check((run("array", std::string("[]"), false), confighttp::writes == 0), "a non-object apps.json leaves the catalogue untouched");
  check((run("no-apps", std::string("{\"x\":1}"), false), confighttp::writes == 0), "an apps.json without an apps array leaves the catalogue untouched");
  // Written: a valid catalogue gains the fullscreen entry; an absent file starts an empty one.
  run("valid", std::string("{\"apps\":[{\"name\":\"A\"}]}"), false);
  check(confighttp::writes == 1 && confighttp::written["apps"].size() == 2 && confighttp::written["apps"][0]["name"] == "A",
        "a valid catalogue gains the fullscreen entry and keeps its apps");
  run("absent", std::nullopt, false);
  check(confighttp::writes == 1 && confighttp::written["apps"].size() == 1, "an absent apps.json starts an empty catalogue with the entry");
  std::cout << checks << " checks; " << failures << " failures\n";
  return failures != 0;
}
"""
code = code.replace('@@APPLY@@', apply)
if "leaving the catalogue untouched" not in apply or apply.count('file_tree["apps"] = nlohmann::json::array();') != 1:
    print('FAIL apply_playnite must leave the catalogue untouched on a failed read and start empty only when the file is absent')
    raise SystemExit(1)
with tempfile.TemporaryDirectory(prefix='playnite-apply-') as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code, encoding='utf-8')
    flags = ['-I', os.environ['AUDIT_JSON_INCLUDE']] if 'AUDIT_JSON_INCLUDE' in os.environ else []
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    *flags, str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test'), str(work)], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'), timeout=60).returncode)
