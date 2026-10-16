#!/usr/bin/env python3
"""Exercise the Windows Steam scan entry points with real temporary VDF files.

Only platform-selection directives are changed for this Linux-host harness: the
Windows branches of steam_integration.cpp run with the existing Windows API
fakes. The production scan functions, parser, filesystem access and identity
scope run unchanged. No host tokens, registry hives or network paths are used.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("repository", nargs="?", type=Path, default=Path(__file__).resolve().parents[4])
parser.add_argument("--source", type=Path)
parser.add_argument("--compiler", default="g++")
args = parser.parse_args()
root = args.repository.resolve()
source = (args.source or root / "src/steam_integration.cpp").read_text()
# Defining _WIN32 globally would select the Windows ABI in libstdc++ itself.
# Select only this translation unit's platform branches instead.
for before, after in (("#ifdef _WIN32", "#if 1"), ("#ifndef _WIN32", "#if 0"),
                      ("#if defined(__linux__)", "#if 0"), ("#ifdef __linux__", "#if 0")):
    source = source.replace(before, after)

boundary = (root / "tests/unit/platform/windows/test_steam_discovery.cpp").read_text()
boundary = boundary[:boundary.index("TEST_F(")]
begin = boundary.index("  auto roots() {")
end = boundary.index("}  // namespace", begin)
boundary = boundary[:begin] + boundary[end:]
boundary = boundary.replace("#include <gtest/gtest.h>", "")
prefix = r'''
#include <iostream>
#include <stdexcept>
#define EXPECT_EQ(a, b) do { if (!((a) == (b))) throw std::runtime_error("boundary: " #a " == " #b); } while (0)
#define EXPECT_FALSE(a) do { if (a) throw std::runtime_error("boundary: !" #a); } while (0)
'''
fixture = Path(__file__).with_name("fixtures") / "steam_discovery_admission.cpp.in"
with tempfile.TemporaryDirectory(prefix="steam-scan-admission-") as temporary:
    work = Path(temporary)
    (work / "windows_scan.cpp").write_text(source)
    (work / "regression.cpp").write_text(prefix + boundary + fixture.read_text())
    binary = work / "regression"
    subprocess.run([args.compiler, "-std=c++20", "-Wall", "-Wextra", "-pthread",
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-I", str(root), "-I", str(root / "src"),
                    "-I", str(root / "tests/support/windows_steam_api"),
                    str(work / "windows_scan.cpp"), str(work / "regression.cpp"),
                    "-o", str(binary)], check=True)
    subprocess.run([str(binary), str(work / "data")], check=True, timeout=30)
