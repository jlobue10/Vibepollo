#!/usr/bin/env python3
"""Compile the production FEC encoder and header writer with real Reed-Solomon.

Check the receiver's 8-bit percentage/10-bit index interpretation, actual parity
recovery, and PyroWave's wire-budget estimate. No GPU or network is required.
Initialize third-party/nanors and third-party/moonlight-common-c first.
--baseline reads production files from HEAD.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '--baseline' in sys.argv


def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
            if BASELINE else (ROOT / path).read_text())


def block(text, marker):
    start = text.index(marker)
    masked = re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"',
                    lambda m: ' ' * len(m.group()), text, flags=re.S)
    end = masked.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (masked[end] == '{') - (masked[end] == '}')
    return text[start:end + 1]


stream = read('src/stream.cpp')
encoder = block(stream, 'namespace fec {')
stamp = re.search(r'inspect->packet\.fecInfo =\s*.*?;', stream, re.S).group()
packet = block(stream, 'struct video_packet_raw_t {') + ';'
descriptor = block(read('src/platform/common.h'), 'struct buffer_descriptor_t {') + ';'
policy = block(read('src/pyrowave_policy.cpp'), 'std::size_t parity_shards(')

test = r'''
#include <cassert>
#include <cstdint>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include "src/utility.h"
FEC_HEADER
extern "C" {
#include "rs.h"
#include "Video.h"
}
using namespace std::literals;
std::ostringstream ignored_log;
#define BOOST_LOG(level) ignored_log
namespace platf { DESCRIPTOR }
PACKET
ENCODER
namespace policy { POLICY }

int failures, checks;
void check(bool ok, const std::string &name) {
  ++checks;
  failures += !ok;
  std::cout << (ok ? "PASS " : "FAIL ") << name << '\n';
}

void run(size_t data, size_t percentage, size_t minimum, bool padded) {
  constexpr size_t bytes = 256;
  std::string name = "data=" + std::to_string(data) + " rate=" + std::to_string(percentage) +
                     " min=" + std::to_string(minimum) + (padded ? " padded" : " aligned");
  std::string payload(data * bytes - (padded ? 7 : 0), '\0');
  for (size_t i = 0; i < payload.size(); ++i) payload[i] = char(i * 31 + 17);
  auto shards = fec::encode(payload, bytes, percentage, minimum, 32);
  bool pointers = true, metadata = shards.percentage <= 255;
  for (size_t x = 0; x < shards.size(); ++x) {
    if (!shards.data(x)) { pointers = false; continue; }
    auto *inspect = reinterpret_cast<video_packet_raw_t *>(shards.data(x));
    STAMP
    const auto info = inspect->packet.fecInfo;
    metadata &= ((info >> 12) & 1023) == x && (info >> 22) == data &&
                ((info >> 4) & 255) == shards.percentage;
  }
  check(pointers && metadata, name + " header fields and shard pointers");
  const auto declared = (data * (shards.percentage & 255) + 99) / 100;
  const auto actual = shards.size() - data;
  check(actual == declared && (!actual || shards.size() <= DATA_SHARDS_MAX), name + " receiver parity count");
  // The planner only accepts blocks within Reed-Solomon capacity. Its estimate
  // must exactly match the packets the encoder emits for those plans.
  const auto planned = policy::parity_shards(data, int(percentage), minimum);
  if (data + planned <= DATA_SHARDS_MAX) {
    check(planned == actual, name + " planner wire budget");
  }
  if (!metadata || !pointers || actual != declared || actual == 0) return;
  std::vector<std::vector<uint8_t>> receive(shards.size(), std::vector<uint8_t>(bytes));
  std::vector<uint8_t *> ptrs(shards.size());
  std::vector<uint8_t> missing(shards.size());
  for (size_t i = 0; i < shards.size(); ++i) {
    std::memcpy(receive[i].data(), shards.data(i), bytes);
    ptrs[i] = receive[i].data();
  }
  // Header fields are stamped after encoding. Check video payload, just as the
  // receiver does after repairing the recovered packet's bookkeeping fields.
  const size_t erased = std::min(data, actual);
  for (size_t i = 0; i < erased; ++i) {
    missing[i] = 1;
    std::fill(receive[i].begin(), receive[i].end(), 0);
  }
  auto *rs = reed_solomon_new(int(data), int(declared));
  assert(rs);
  bool recovered = reed_solomon_decode(rs, ptrs.data(), missing.data(), int(shards.size()), bytes) == 0;
  reed_solomon_release(rs);
  for (size_t i = 0; i < erased; ++i) {
    recovered &= std::memcmp(receive[i].data() + sizeof(video_packet_raw_t),
                             shards.data(i) + sizeof(video_packet_raw_t), bytes - sizeof(video_packet_raw_t)) == 0;
  }
  check(recovered, name + " reconstruct missing data with advertised parity");
}

int main() {
  reed_solomon_init();
  for (bool padded : {false, true}) {
    run(1, 20, 2, padded);
    run(1, 20, 5, padded);
    run(1, 20, 64, padded);
    run(7, 20, 2, padded);
    run(150, 1, 64, padded);
    run(200, 20, 64, padded);
    run(254, 1, 2, padded);
    run(255, 1, 2, padded);
    run(300, 0, 64, padded);
    run(1, 0, 64, padded);
  }
  std::cout << checks << " checks; " << failures << " failures\n";
  return failures ? 1 : 0;
}
'''

with tempfile.TemporaryDirectory(prefix='video-fec-wire-') as directory:
    work = Path(directory)
    # The shared wire policy is new with this fix; older baselines do not use it.
    if 'video_fec::' in encoder:
        (work / 'video_fec.h').write_text(read('src/video_fec.h'))
        fec_header = '#include "video_fec.h"'
    else:
        fec_header = ''
    for key, value in [('FEC_HEADER', fec_header), ('DESCRIPTOR', descriptor), ('PACKET', packet),
                       ('ENCODER', encoder), ('POLICY', policy), ('STAMP', stamp)]:
        test = test.replace(key, value)
    (work / 'test.cpp').write_text(test)
    nanors = ROOT / 'third-party/nanors'
    flags = ['-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-pie']
    subprocess.run(['cc', *flags, '-I' + str(nanors / 'deps/obl'), '-c', str(nanors / 'rs.c'),
                    '-o', str(work / 'rs.o')], check=True)
    includes = [ROOT, work, nanors, ROOT / 'third-party/moonlight-common-c/src']
    if os.environ.get('AUDIT_JSON_INCLUDE'):
        includes.append(Path(os.environ['AUDIT_JSON_INCLUDE']))
    subprocess.run(['g++', '-std=c++20', *flags, '-no-pie', *['-I' + str(p) for p in includes],
                    str(work / 'test.cpp'), str(work / 'rs.o'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
