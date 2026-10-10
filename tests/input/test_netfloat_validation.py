#!/usr/bin/env python3
"""Execute the production netfloat conversion against NaN and infinite payloads.

A NaN compares false against both clamp bounds and used to pass through
from_clamped_netfloat into float-to-integer conversions. No devices are used;
--baseline reads src/input.cpp from HEAD.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'src/input.cpp'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())

def block(text, marker):
    start = text.index(marker)
    end = text.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (text[end] == '{') - (text[end] == '}')
    return text[start:end + 1]

prefix = r"""
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
typedef unsigned char netfloat[4];
namespace boost::endian {
  enum class order { little };
  template<class T, std::size_t N, order O> T endian_load(const unsigned char *p) { T v; std::memcpy(&v, p, N); return v; }
}
"""
suffix = r"""
int main() {
  int failures = 0, checks = 0;
  auto check = [&](bool ok, const char *name) { ++checks; failures += !ok; std::cout << (ok ? "PASS " : "FAIL ") << name << '\n'; };
  auto clamped = [](float value, float lo, float hi) { netfloat f; std::memcpy(f, &value, sizeof(f)); return from_clamped_netfloat(f, lo, hi); };
  check(clamped(std::numeric_limits<float>::quiet_NaN(), 0.0f, 1.0f) == 0.0f, "NaN clamps to the minimum");
  check(clamped(-std::numeric_limits<float>::quiet_NaN(), 0.0f, 1.0f) == 0.0f, "negative NaN clamps to the minimum");
  check(clamped(std::numeric_limits<float>::signaling_NaN(), -1.0f, 1.0f) == -1.0f, "signalling NaN clamps to the minimum");
  check(clamped(std::numeric_limits<float>::infinity(), 0.0f, 1.0f) == 1.0f, "+Inf clamps to the maximum");
  check(clamped(-std::numeric_limits<float>::infinity(), 0.0f, 1.0f) == 0.0f, "-Inf clamps to the minimum");
  check(clamped(0.5f, 0.0f, 1.0f) == 0.5f, "in-range values pass through");
  check(clamped(2.0f, 0.0f, 1.0f) == 1.0f && clamped(-3.0f, -1.0f, 1.0f) == -1.0f, "finite values clamp to the bounds");
  check(!std::isnan(clamped(std::numeric_limits<float>::quiet_NaN(), 0.0f, 1.0f)), "the result is never NaN");
  std::cout << checks << " checks; " << failures << " failures\n";
  return failures ? 1 : 0;
}
"""
code = prefix + '\n'.join(block(source, m) for m in ['float from_netfloat(netfloat f)', 'float from_clamped_netfloat(netfloat f, float min, float max)']) + suffix
with tempfile.TemporaryDirectory(prefix='netfloat-') as directory:
    work = Path(directory)
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
