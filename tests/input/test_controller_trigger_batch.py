#!/usr/bin/env python3
"""Execute production controller batching with representative queued trigger states.

Only the packet fields used by the overload are modeled; no OS input or driver
is invoked. --baseline reads HEAD instead of the edited source.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/input.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/input.cpp').read_text())
marker = 'batch_result_e batch(PNV_MULTI_CONTROLLER_PACKET dest, PNV_MULTI_CONTROLLER_PACKET src)'
start = source.index(marker)
end = source.index('{', start)
depth = 1
while depth:
    end += 1
    depth += (source[end] == '{') - (source[end] == '}')
function = source[start:end + 1]
prefix = r'''
#include <cstdint>
#include <cstdio>
struct packet {
    uint16_t activeGamepadMask = 1, controllerNumber = 0, buttonFlags = 0, buttonFlags2 = 0;
    uint8_t leftTrigger = 0, rightTrigger = 0;
    int16_t leftStickX = 0;
};
using PNV_MULTI_CONTROLLER_PACKET = packet*;
enum class batch_result_e {batched, not_batchable, terminate_batch};
'''
suffix = r'''
int main() {
    int checks = 0, failures = 0;
    auto check = [&](bool ok, const char* message) {
        ++checks; failures += !ok;
        printf("%s %s\n", ok ? "PASS" : "FAIL", message);
    };
    for (bool right : {false, true}) {
        for (bool release : {false, true}) {
            packet a, b;
            (right ? a.rightTrigger : a.leftTrigger) = release ? 255 : 0;
            (right ? b.rightTrigger : b.leftTrigger) = release ? 0 : 255;
            const packet original = a;
            check(batch(&a, &b) == batch_result_e::terminate_batch &&
                  a.leftTrigger == original.leftTrigger && a.rightTrigger == original.rightTrigger,
                  "trigger press/release is a barrier and leaves the earlier state intact");
        }
    }
    packet a, b;
    a.leftTrigger = 80; b.leftTrigger = 200; b.leftStickX = 1234;
    check(batch(&a, &b) == batch_result_e::batched && a.leftTrigger == 200 && a.leftStickX == 1234,
          "continuous trigger and stick travel still take the newest state");
    for (bool right : {false, true}) {
        for (bool release : {false, true}) {
            packet before, after;
            (right ? before.rightTrigger : before.leftTrigger) = release ? 240 : 239;
            (right ? after.rightTrigger : after.leftTrigger) = release ? 239 : 240;
            check(batch(&before, &after) == batch_result_e::terminate_batch,
                  "Steam Controller full-pull click is retained without a return to rest");
        }
    }
    b.controllerNumber = 1;
    check(batch(&a, &b) == batch_result_e::not_batchable, "unrelated controller remains skippable");
    b.controllerNumber = 0; b.activeGamepadMask = 3;
    check(batch(&a, &b) == batch_result_e::terminate_batch, "controller membership remains a barrier");
    b.activeGamepadMask = 1; b.buttonFlags = 1;
    check(batch(&a, &b) == batch_result_e::terminate_batch, "ordinary buttons remain a barrier");
    b.buttonFlags = 0; b.buttonFlags2 = 1;
    check(batch(&a, &b) == batch_result_e::terminate_batch, "extended buttons remain a barrier");
    printf("%d checks, %d failures\n", checks, failures);
    return failures != 0;
}
'''
with tempfile.TemporaryDirectory(prefix='trigger-batch-') as directory:
    work = Path(directory)
    (work / 'test.cpp').write_text('#include <initializer_list>\n' + prefix + function + suffix)
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')]).returncode)
