#!/usr/bin/env python3
"""Run the three production mouse/scroll batching overloads at signed-16-bit boundaries.

The wire fields and endian conversion are modeled; arithmetic and admission are
the actual input.cpp functions. --baseline reads HEAD instead of the working tree.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/input.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/input.cpp').read_text())


def function(packet):
    marker = f'batch_result_e batch({packet} dest, {packet} src)'
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


prefix = r'''
#include <bit>
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <limits>
namespace util::endian {
short big(short value) {
    if constexpr (std::endian::native == std::endian::little) {
        return static_cast<short>(__builtin_bswap16(static_cast<uint16_t>(value)));
    }
    return value;
}
}
struct relative_packet { short deltaX, deltaY; };
struct scroll_packet { short scrollAmt1, scrollAmt2; };
struct hscroll_packet { short scrollAmount; };
using PNV_REL_MOUSE_MOVE_PACKET = relative_packet*;
using PNV_SCROLL_PACKET = scroll_packet*;
using PSS_HSCROLL_PACKET = hscroll_packet*;
enum class batch_result_e { batched, not_batchable, terminate_batch };
'''
suffix = r'''
int main() {
    int checks = 0, failures = 0;
    auto check = [&](bool ok, const char* label) {
        ++checks; failures += !ok;
        printf("%s %s\n", ok ? "PASS" : "FAIL", label);
    };
    const auto wire = util::endian::big;
    for (auto values : {relative_packet{100, 200}, relative_packet{-100, -200},
                       relative_packet{32760, 7}, relative_packet{-32760, -8},
                       relative_packet{30000, 30000}, relative_packet{-30000, -30000},
                       relative_packet{32767, -32768}}) {
        const int a = values.deltaX, b = values.deltaY, sum = a + b;
        const bool fits = sum >= -32768 && sum <= 32767;
        const auto expected = fits ? batch_result_e::batched : batch_result_e::terminate_batch;
        relative_packet rel{wire(a), wire(-100)}, next{wire(b), wire(50)};
        check(batch(&rel, &next) == expected &&
              util::endian::big(rel.deltaX) == (fits ? sum : a) &&
              util::endian::big(rel.deltaY) == (fits ? -50 : -100),
              "relative X batches exactly representable sums and preserves rejected input");
        rel = {wire(100), wire(a)}; next = {wire(-50), wire(b)};
        check(batch(&rel, &next) == expected &&
              util::endian::big(rel.deltaX) == (fits ? 50 : 100) &&
              util::endian::big(rel.deltaY) == (fits ? sum : a),
              "relative Y overflow leaves both axes unchanged");
        scroll_packet vertical{wire(a), wire(a)}, vnext{wire(b), wire(b)};
        check(batch(&vertical, &vnext) == expected &&
              util::endian::big(vertical.scrollAmt1) == (fits ? sum : a) &&
              vertical.scrollAmt1 == vertical.scrollAmt2,
              "vertical scroll batches without wrapping or disagreeing duplicate fields");
        hscroll_packet horizontal{wire(a)}, hnext{wire(b)};
        check(batch(&horizontal, &hnext) == expected &&
              util::endian::big(horizontal.scrollAmount) == (fits ? sum : a),
              "horizontal scroll batches without wrapping");
    }
    relative_packet both{wire(30000), wire(-30000)}, next{wire(30000), wire(-30000)};
    check(batch(&both, &next) == batch_result_e::terminate_batch &&
          util::endian::big(both.deltaX) == 30000 && util::endian::big(both.deltaY) == -30000,
          "simultaneous axis overflows cannot reverse the cursor direction");
    printf("%d checks, %d failures\n", checks, failures);
    return failures != 0;
}
'''
with tempfile.TemporaryDirectory(prefix='mouse-batch-arithmetic-') as directory:
    work = Path(directory)
    code = prefix + '\n'.join(function(packet) for packet in (
        'PNV_REL_MOUSE_MOVE_PACKET', 'PNV_SCROLL_PACKET', 'PSS_HSCROLL_PACKET')) + suffix
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-Wall', '-Wextra', '-g',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')]).returncode)
