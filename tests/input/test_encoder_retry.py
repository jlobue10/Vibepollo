#!/usr/bin/env python3
"""Run the production async encoder retry policy for Linux and Windows builds."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/video.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/video.cpp').read_text())
start = source.index('    auto hdr_event = mail->event<hdr_info_t>(mail::hdr);', source.index('void capture_async('))
declarations = source[source.index('\n', start):source.index('    // Encoding takes place', start)]
start = source.index('      if (encode_result == encode_run_result_e::initialization_failed)', start)
end = source.index('\n    }\n  }', start)
policy = source[start:end].replace('std::this_thread::sleep_for', 'record_delay')
prefix = r'''
#include <chrono>
#include <iostream>
#include <string_view>
#include <vector>
using namespace std::literals;
#define BOOST_LOG(level) std::clog
enum class encode_run_result_e { initialization_failed, temporarily_busy, runtime_failed, ok };
int amdvce_experimental = 1, amdvce_ffmpeg = 2, nvenc = 3, software = 4;
struct { bool is_quarantined() { return false; } } native_amf_lifecycle_gate;
std::vector<int> delays;
void record_delay(std::chrono::milliseconds d) { delays.push_back(d.count()); }
int attempts;
void run(const int &session_encoder, const std::vector<encode_run_result_e> &results) {
    DECLARATIONS
    for (const auto encode_result : results) {
        ++attempts;
        POLICY
    }
}
int main() {
    using R = encode_run_result_e;
    int failures = 0;
    auto check = [&](bool ok, const char *name) {
        failures += !ok;
        std::cout << (ok ? "PASS " : "FAIL ") << name << '\n';
    };
    for (int *encoder : {&software, &nvenc}) {
        attempts = 0; delays.clear();
        run(*encoder, {R::initialization_failed, R::initialization_failed,
                       R::initialization_failed, R::initialization_failed});
        check(attempts == 3 && delays == std::vector<int>({100, 200}),
              "persistent initialization failure backs off and ends after three attempts");
    }
    attempts = 0; delays.clear();
    run(software, {R::initialization_failed, R::ok, R::initialization_failed,
                   R::initialization_failed, R::ok});
    check(attempts == 5 && delays == std::vector<int>({100, 100, 200}),
          "a successful encode resets the failure budget");
    // A mid-stream convert/encode failure shares the budget: it backs off and ends after
    // three attempts instead of rebuilding the encoder in a tight loop.
    attempts = 0; delays.clear();
    run(software, {R::runtime_failed, R::runtime_failed, R::runtime_failed, R::runtime_failed});
    check(attempts == 3 && delays == std::vector<int>({100, 200}),
          "persistent mid-stream failure backs off and ends after three attempts");
    attempts = 0; delays.clear();
    run(software, {R::runtime_failed, R::ok, R::initialization_failed, R::runtime_failed, R::ok});
    check(attempts == 5 && delays == std::vector<int>({100, 100, 200}),
          "mid-stream and initialization failures share one budget that a good run resets");
    return failures != 0;
}
'''
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    failures = 0
    for name, defines in [('linux', ''), ('linux-cuda', '#define SUNSHINE_BUILD_CUDA 1\n'),
                          ('windows', '#define _WIN32 1\n')]:
        # Set the platform selector after standard headers; this is a policy
        # fixture on the current host, not a cross build of standard libraries.
        code = prefix.replace('enum class encode_run_result_e', defines + 'enum class encode_run_result_e')
        code = code.replace('DECLARATIONS', declarations).replace('POLICY', policy)
        (work / 'test.cpp').write_text(code)
        subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
        print(name, flush=True)
        failures += subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode != 0
    raise SystemExit(failures != 0)
