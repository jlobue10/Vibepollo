#!/usr/bin/env python3
"""Production controller-touch batching with recorded packets and no OS input."""
import pathlib,subprocess,tempfile,sys
from test_delayed_mouse_release import function
ROOT=pathlib.Path(__file__).resolve().parents[2]
s=subprocess.check_output(['git','show','HEAD:src/input.cpp'],cwd=ROOT,text=True) if '--baseline' in sys.argv else (ROOT/'src/input.cpp').read_text()
pre=r'''
#include <cstdint>
#include <cstdio>
enum {LI_TOUCH_EVENT_HOVER=0,LI_TOUCH_EVENT_DOWN=1,LI_TOUCH_EVENT_MOVE=3,LI_TOUCH_EVENT_CANCEL_ALL=7};
struct packet {uint8_t controllerNumber,eventType,touchpadIndex;uint32_t pointerId;float x;};
using PSS_CONTROLLER_TOUCH_PACKET=packet*;
enum class batch_result_e {batched,not_batchable,terminate_batch};
'''
post=r'''
int main(){int failures=0;
 auto check=[&](bool ok,const char* name){printf("%s %s\n",ok?"PASS":"FAIL",name);if(!ok)++failures;};
 packet a{0,LI_TOUCH_EVENT_MOVE,1,0,0.2f},b{0,LI_TOUCH_EVENT_MOVE,0,0,0.9f};
 check(batch(&a,&b)==batch_result_e::not_batchable&&a.x==0.2f,"different physical pads retain independent motion");
 b.touchpadIndex=1;check(batch(&a,&b)==batch_result_e::batched&&a.x==0.9f,"same pad and pointer take newest motion");
 b.eventType=LI_TOUCH_EVENT_CANCEL_ALL;b.touchpadIndex=0;
 check(batch(&a,&b)==batch_result_e::terminate_batch,"cancel-all on the other pad remains an ordering barrier");
 b.eventType=LI_TOUCH_EVENT_DOWN;
 check(batch(&a,&b)==batch_result_e::terminate_batch,"cross-pad contact changes preserve event order");
 b.controllerNumber=1;check(batch(&a,&b)==batch_result_e::not_batchable,"unrelated controllers do not stop eligible batching");
 return failures!=0;
}
'''
with tempfile.TemporaryDirectory(prefix='touch-batch-') as d:
 p=pathlib.Path(d);(p/'test.cpp').write_text(pre+function(s,'batch_result_e batch(PSS_CONTROLLER_TOUCH_PACKET dest, PSS_CONTROLLER_TOUCH_PACKET src)')+post)
 subprocess.run(['g++','-std=c++20','-fsanitize=address,undefined','-g',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 raise SystemExit(subprocess.run([str(p/'test')]).returncode)
