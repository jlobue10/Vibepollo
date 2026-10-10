#!/usr/bin/env python3
"""Exercise production VHF/ViGEm feedback dispatch and mailbox policy without a driver.

--baseline reads HEAD. AUDIT_JSON_INCLUDE locates nlohmann/json on minimal hosts.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
            if '--baseline' in sys.argv else (ROOT / path).read_text())
def block(text, marker):
    start = text.index(marker)
    end = text.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (text[end] == '{') - (text[end] == '}')
    return text[start:end + 1]

common = read('src/platform/common.h')
types = common[common.index('  enum class gamepad_feedback_e'):common.index('  using feedback_queue_t')]
helpers = '\n'.join(block(common, x) for x in ['inline unsigned steam_haptic_stop_key', '  inline unsigned gamepad_feedback_state_key', '  inline bool raise_gamepad_feedback'])
policy = read('src/platform/windows/vhf_gamepad_policy.h')
feedback_types = '\n'.join(block(policy, x) + ';' for x in ['  struct trigger_effect_t', '  struct rumble_rgb_t'])
body = block(read('src/platform/windows/vhf_gamepad.cpp'), '  void vhf_gamepad_t::impl_t::raise_feedback(').replace('vhf_gamepad_t::impl_t::', '')
vigem = read('src/platform/windows/input.cpp')
alloc = vigem[vigem.index('      gamepad.client_relative_index = id.clientRelativeIndex;'):vigem.index('      gamepad.last_report_ts =', vigem.index('    int alloc_gamepad_internal('))]
vigem_methods = '\n'.join(block(vigem, x) for x in ['    void rumble(', '    void set_rgb_led('])
code = r'''
#include <array>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <map>
#include <vector>
#include "thread_safe_test.h"
using namespace std::literals;
namespace config {struct {bool forward_rumble=true;} input;}
TYPES
HELPERS
namespace vhf_gamepad {FEEDBACK_TYPES}
struct slot_t {
 bool active=true,have_feedback=false;std::uint16_t client_relative_index=3;
 vhf_gamepad::rumble_rgb_t last_feedback{};
 std::shared_ptr<safe::queue_t<gamepad_feedback_msg_t>> feedback_queue;
};
std::array<slot_t,1> slots;
BODY
struct VigemHarness {
 struct target_t {using pointer=void*;};
 struct Gamepad {
  struct Target {void* get(){return reinterpret_cast<void*>(1);}} gp;
  int client_relative_index=3;bool have_last_rumble=false,have_last_rgb_led=false;
  gamepad_feedback_msg_t last_rumble{},last_rgb_led{};
  std::map<std::uint64_t,std::uint8_t> pointer_id_map;
  std::shared_ptr<safe::queue_t<gamepad_feedback_msg_t>> feedback_queue;
 };
 std::array<Gamepad,1> gamepads;
 void allocate(){auto& gamepad=gamepads[0];struct {int clientRelativeIndex=3;} id;ALLOC}
 VIGEM_METHODS
};
int main(){
 int failures=0,checks=0;
 auto check=[&](bool ok,const char* text){++checks;std::cout<<(ok?"PASS ":"FAIL ")<<text<<'\n';failures+=!ok;};
 auto q=std::make_shared<safe::queue_t<gamepad_feedback_msg_t>>(256);
 auto reset=[&]{q->reset();slots[0]=slot_t{};slots[0].feedback_queue=q;};
 auto drain=[&]{std::vector<gamepad_feedback_msg_t> result;while(auto m=q->pop(0ms))result.push_back(*m);return result;};
 auto only=[&](const std::vector<gamepad_feedback_msg_t>& events,gamepad_feedback_e kind){return events.size()==1&&events[0].type==kind&&events[0].id==3;};
 reset();config::input.forward_rumble=false;
 vhf_gamepad::rumble_rgb_t f{};f.has_rgb=true;f.red=42;f.low_frequency=1234;
 f.has_triggers=true;f.left_trigger=222;f.has_trigger_effects=true;f.trigger_event_flags=12;f.left_effect.mode=1;
 raise_feedback(0,f);auto events=drain();
 check(only(events,gamepad_feedback_e::set_rgb_led)&&events[0].data.rgb_led.r==42,
       "rumble disabled still forwards the LED while withholding all motor/effect messages");
 for(int i=0;i<250;i++)raise_feedback(0,f);
 check(drain().empty(),"unchanged disabled-rumble feedback produces no duplicate LED traffic");
 f.red=0;raise_feedback(0,f);events=drain();
 check(only(events,gamepad_feedback_e::set_rgb_led)&&events[0].data.rgb_led.r==0,
       "rumble disabled still forwards an explicit LED-off update");

 reset();config::input.forward_rumble=true;f={};f.has_rgb=true;f.red=42;f.has_triggers=true;f.left_trigger=222;
 raise_feedback(0,f);drain();
 vhf_gamepad::rumble_rgb_t motor{};motor.low_frequency=555;raise_feedback(0,motor);events=drain();
 check(only(events,gamepad_feedback_e::rumble),"motor-only feedback does not turn off LED or trigger rumble");
 f.low_frequency=555;raise_feedback(0,f);
 check(drain().empty(),"motor-only feedback does not invalidate unchanged LED/trigger state");
 raise_feedback(0,motor);drain();f.red=0;f.left_trigger=0;raise_feedback(0,f);events=drain();
 bool ledOff=false,triggerStop=false;
 for(auto& e:events){
  ledOff|=e.type==gamepad_feedback_e::set_rgb_led&&e.data.rgb_led.r==0;
  triggerStop|=e.type==gamepad_feedback_e::rumble_triggers&&e.data.rumble_triggers.left_trigger==0&&e.data.rumble_triggers.right_trigger==0;
 }
 check(ledOff,"explicit LED off survives an intervening packet without RGB");
 check(triggerStop,"explicit trigger stop survives an intervening packet without trigger state");

 reset();f={};f.has_rgb=true;raise_feedback(0,f);events=drain();bool firstBlack=false;
 for(auto& e:events)firstBlack|=e.type==gamepad_feedback_e::set_rgb_led;
 check(firstBlack,"first black LED state is delivered to a fresh slot");

 VigemHarness v;v.gamepads[0].feedback_queue=q;config::input.forward_rumble=true;q->reset();v.allocate();
 v.rumble(reinterpret_cast<void*>(1),0,0);events=drain();
 check(only(events,gamepad_feedback_e::rumble),"fresh ViGEm slot forwards its first explicit motor stop");
 v.set_rgb_led(reinterpret_cast<void*>(1),0,0,0);events=drain();
 check(only(events,gamepad_feedback_e::set_rgb_led),"fresh ViGEm slot forwards its first explicit black LED");
 for(int i=0;i<250;i++){v.rumble(reinterpret_cast<void*>(1),0,0);v.set_rgb_led(reinterpret_cast<void*>(1),0,0,0);}
 check(drain().empty(),"ViGEm deduplicates successfully delivered zero states");
 v.allocate();v.rumble(reinterpret_cast<void*>(1),0,0);v.set_rgb_led(reinterpret_cast<void*>(1),0,0,0);events=drain();
 check(events.size()==2,"reused ViGEm slot resends zero states to its new client");
 v.allocate();q->stop();v.set_rgb_led(reinterpret_cast<void*>(1),0,0,0);q->reset();
 v.set_rgb_led(reinterpret_cast<void*>(1),0,0,0);events=drain();
 check(only(events,gamepad_feedback_e::set_rgb_led),"failed ViGEm queue delivery leaves the first LED state retryable");
 std::cout<<checks<<" checks, "<<failures<<" failures\n";return failures!=0;
}
'''.replace('FEEDBACK_TYPES', feedback_types).replace('TYPES', types).replace('HELPERS', helpers).replace('BODY', body).replace('VIGEM_METHODS', vigem_methods).replace('ALLOC', alloc)
with tempfile.TemporaryDirectory(prefix='vhf-feedback-') as directory:
    work = Path(directory)
    (work / 'thread_safe_test.h').write_text(read('src/thread_safe.h'))
    (work / 'test.cpp').write_text(code)
    args = ['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-I' + str(ROOT / 'src')]
    if os.getenv('AUDIT_JSON_INCLUDE'):
        args += ['-I' + os.environ['AUDIT_JSON_INCLUDE']]
    subprocess.run(args + [str(work / 'test.cpp'), '-pthread', '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=os.environ).returncode)
