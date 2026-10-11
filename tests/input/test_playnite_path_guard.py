#!/usr/bin/env python3
"""Compile the production Playnite media-path guard (is_safe_local_media_path in
src/platform/windows/playnite_sync.cpp) and check what it accepts, then check that both SYSTEM-side
opens (cover conversion, icon resolution) are gated by it.

The host opens these paths as SYSTEM on a Playnite peer's say-so (WIC decode, LoadLibraryEx as a data
file); a UNC path forces SYSTEM network authentication and a device/pipe namespace path hands a pipe
squatter the impersonation client PR #39 closed. --baseline reads HEAD.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYNC = 'src/platform/windows/playnite_sync.cpp'


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
#include <cctype>
#include <cstddef>
#include <iostream>
#include <string>
#include <string_view>
namespace platf::playnite::sync {
"""

MAIN = r"""
}  // namespace platf::playnite::sync
int main() {
  using platf::playnite::sync::is_safe_local_media_path;
  int failures = 0;
  auto check = [&](bool ok, const char *name) { std::cout << (ok ? "PASS: " : "FAIL: ") << name << '\n'; failures += !ok; };
  check(is_safe_local_media_path("C:\\x\\a.png"), "a drive-rooted local file is accepted");
  check(is_safe_local_media_path("d:/Games/Some Game/game.exe"), "forward slashes and a lower-case drive are accepted");
  check(is_safe_local_media_path("C:\\Program Files (x86)\\Game\\icon.ico"), "spaces and parentheses are accepted");
  check(!is_safe_local_media_path("\\\\srv\\share\\a.png"), "a UNC path is refused");
  check(!is_safe_local_media_path("//srv/share/a.png"), "a forward-slash UNC path is refused");
  check(!is_safe_local_media_path("\\\\.\\pipe\\squatter"), "a device namespace path is refused");
  check(!is_safe_local_media_path("\\\\?\\C:\\a.png"), "an extended-length prefix is refused");
  check(!is_safe_local_media_path("a.png"), "a relative path is refused");
  check(!is_safe_local_media_path("C:a.png"), "a drive-relative path is refused");
  check(!is_safe_local_media_path("C:\\x\\..\\..\\Windows\\win.ini"), "a '..' segment is refused");
  check(!is_safe_local_media_path("C:\\x\\.\\a.png"), "a '.' segment is refused");
  check(!is_safe_local_media_path("C:\\x\\\\a.png"), "an empty segment is refused");
  check(!is_safe_local_media_path("C:\\x\\"), "a directory with a trailing separator is refused");
  check(!is_safe_local_media_path("C:\\x\\a.png:stream"), "an alternate data stream is refused");
  check(!is_safe_local_media_path(std::string("C:\\x\\a.png\0\\\\srv", 16)), "an embedded NUL is refused");
  check(!is_safe_local_media_path(""), "an empty path is refused");
  return failures ? 1 : 0;
}
"""


def main():
    source = read(SYNC)
    failures = 0
    try:
        guard = function(source, 'bool is_safe_local_media_path(const std::string &path)')
    except ValueError:
        print('FAIL: is_safe_local_media_path is missing')
        return 1
    convert = function(source, 'bool convert_playnite_image_to_png(const std::string &src_path, const std::filesystem::path &dst)')
    apply = function(source, 'void apply_game_metadata_to_app(const Game &game, nlohmann::json &app, const std::filesystem::path &covers_root)')
    for ok, name in (
        (convert.index('resolve_local_media_path(src_path)') < convert.index('convert_to_png_96dpi'), 'the cover conversion checks the source path before WIC opens it'),
        ('resolve_local_media_path(game.icon_path)' in apply and 'resolve_local_media_path(game.exe)' in apply and apply.index('resolve_local_media_path(game.exe)') < apply.index('resolve_best_app_icon_png'), 'the icon resolver only sees checked icon/exe paths'),
        ('resolve_local_media_path(install_dir)' in apply and apply.index('resolve_local_media_path(install_dir)') < apply.index('resolve_best_app_icon_png'), 'the icon resolver only sees a checked install dir'),
    ):
        print(('PASS: ' if ok else 'FAIL: ') + name)
        failures += not ok
    # The web UI's icon path and the executable scan are gated the same way (H23-02/H23-03).
    integration = read('src/platform/windows/playnite_integration.cpp')
    web_icon = function(integration, 'bool get_icon_png_for_playnite_game(const std::string &playnite_id, std::string &out_path)')
    convert_cpp = read('src/platform/windows/image_convert.cpp')
    scan = function(convert_cpp, 'fs::path find_primary_executable(const std::wstring &exe_hint, const std::wstring &install_dir)')
    resolve = function(source, 'std::optional<std::wstring> resolve_local_media_path(const std::string &path)')
    for ok, name in (
        (web_icon.count('resolve_local_media_path(') >= 3 and web_icon.index('resolve_local_media_path(') < web_icon.index('resolve_best_app_icon_png'),
         'the web UI icon path resolves icon, exe and install dir through the handle check first'),
        ('FILE_ATTRIBUTE_REPARSE_POINT' in scan, 'the executable scan skips reparse points'),
        ('FILE_FLAG_OPEN_REPARSE_POINT' in resolve and 'SECURITY_IDENTIFICATION' in resolve and 'GetFinalPathNameByHandleW' in resolve and 'is_safe_local_media_path' in resolve,
         'the handle check refuses a final reparse point, grants no impersonation and re-checks the resolved path'),
    ):
        print(('PASS: ' if ok else 'FAIL: ') + name)
        failures += not ok
    with tempfile.TemporaryDirectory(prefix='playnite-path-guard-') as temp:
        cpp, binary = Path(temp) / 'test.cpp', Path(temp) / 'test'
        cpp.write_text(PRELUDE + guard + MAIN, encoding='utf-8')
        subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-fsanitize=address,undefined', str(cpp), '-o', str(binary)], check=True)
        failures += subprocess.run([str(binary)]).returncode
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
