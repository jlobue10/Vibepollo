#!/usr/bin/env python3
"""Exercise the production custom-surround validation for every digit layout.

Opus requires at least one stream and no more coupled streams than streams.
The previous n + m == channels check accepted layouts that return a null encoder.
Invalid optional layouts must leave CUSTOM_SURROUND_PARAMS disabled so capture
and encoding use the same default layout. No audio device or Opus headers needed.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/rtsp.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/rtsp.cpp').read_text())
start = source.index('      // Channels', source.index('session->surround_params.length() > 3'))
end_marker = '      config.audio.flags[audio::config_t::CUSTOM_SURROUND_PARAMS] = valid;'
validation = source[start:source.index(end_marker, start) + len(end_marker)]
code = r'''
#include <cstdint>
#include <iostream>
#include <string>
namespace audio { struct config_t { enum { CUSTOM_SURROUND_PARAMS }; }; }
struct params_t { std::uint8_t channelCount{}, streams{}, coupledStreams{}, mapping[8]{}; };
bool accepted(const std::string &layout, int channels) {
  struct {std::string surround_params;} launch {layout}; auto *session = &launch;
  struct {struct {int channels; params_t customStreamParams; bool flags[1]{};} audio;} config {{channels}};
  if (layout.size() > 3) {
VALIDATION
  }
  return config.audio.flags[0];
}
int main() {
  int checks = 0, failures = 0;
  auto check = [&](bool ok, const std::string &name) {
    ++checks;
    if (!ok) {++failures; std::cout << "FAIL " << name << '\n';}
  };
  for (int channels : {6, 8}) {
    const auto mapping = std::string("01234567").substr(0, channels);
    for (int streams = 0; streams < 10; ++streams) {
      for (int coupled = 0; coupled < 10; ++coupled) {
        const auto layout = std::to_string(channels) + std::to_string(streams) + std::to_string(coupled) + mapping;
        check(accepted(layout, channels) == (streams > 0 && coupled <= streams && streams + coupled == channels), layout);
      }
    }
  }
  for (const auto *invalid : {"", "633", "63301234", "6330123456", "63301234/", "633012346", "6x3012345", "606012345", "81501234567"}) {
    check(!accepted(invalid, 6), invalid);
  }
  check(!accepted("84401234567", 6), "channel mismatch");
  check(accepted("633012345", 6), "Moonlight normal 5.1");
  check(accepted("660012345", 6), "Moonlight high quality 5.1");
  check(accepted("85301234567", 8), "Moonlight normal 7.1");
  check(accepted("88001234567", 8), "Moonlight high quality 7.1");
  std::cout << checks << " surround layout checks; " << failures << " failures\n";
  return failures != 0;
}
'''.replace('VALIDATION', validation)
with tempfile.TemporaryDirectory(prefix='surround-layout-') as directory:
    work = Path(directory)
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
