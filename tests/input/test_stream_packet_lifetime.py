#!/usr/bin/env python3
"""Exercise production packet admission, queueing and replacement ownership.

The session model omits GPU/socket work but uses the broadcast threads' actual
channel admission and first session reads, the real safe::queue_t, packet base,
and encoder replacement declaration/publication. --baseline reads HEAD and
reproduces the retired-session/vector/NAL accesses under ASan.
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
            if BASELINE else (ROOT/path).read_text())

def block(text, marker):
    start = text.index(marker)
    masked = re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"',
                    lambda m: ' ' * len(m.group()), text, flags=re.S)
    end = masked.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (masked[end] == '{') - (masked[end] == '}')
    return text[start:end+1]

stream = read('src/stream.cpp')
video = read('src/video.cpp')
header = read('src/video.h')
audio = read('src/audio.h')
guarded = 'packet_channel->close();' in stream
video_loop = block(stream, 'void videoBroadcastThread(')
start = video_loop.index('      if (!packet->channel_data)') if guarded else video_loop.index('      auto session =')
video_admit = video_loop[start:video_loop.index('      const auto packet_pop_timestamp')]
audio_loop = block(stream, 'void audioBroadcastThread(')
audio_admit = audio_loop[audio_loop.index('      TUPLE_2D_REF'):audio_loop.index('      auto sequenceNumber')]
packet_base = block(header, 'struct packet_raw_t') + ';'
replacement_decl = re.search(r'^    std::[^\n]*replacements[^\n]*;', video, re.M).group().strip()
replacement_publish = re.search(r'^      packet->replacements =[^\n]*;', video, re.M).group().strip()
audio_type = re.search(r'  using packet_t =[^\n]*;', audio).group().strip()
join = block(stream, 'void join(session_t &session,')
close = re.search(r'        session.packet_channel->close\(\);', join).group().strip() if guarded else ''
if guarded:
    assert join.index(close) > join.index('session.audioThread.join();')
    assert join.index(close) > join.index('session.controlEnd.view();')
    assert join.index(close) < join.index('std::unique_lock<std::mutex> lifecycle_lock')
channel = 'session.packet_channel' if guarded else '&session'
prefix = r'''
#include <cassert>
#include <chrono>
#include <future>
#include <iostream>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <vector>
#include "src/thread_safe.h"
CHANNEL_INCLUDE
using namespace std::literals;
struct SS_HDR_METADATA {};
namespace video {
PACKET_BASE
struct packet_impl: packet_raw_t {
 bool is_idr() override {return true;}
 int64_t frame_index() override {return 0;}
 uint8_t* data() override {return nullptr;}
 size_t data_size() override {return 0;}
};
using packet_t = std::unique_ptr<packet_impl>;
struct encoder_t {REPLACEMENT_DECL};
}
namespace audio {using buffer_t = std::vector<uint8_t>; AUDIO_TYPE}
struct session_t {
 CHANNEL_FIELD
 CHANNEL_DESTRUCTOR
 struct {int lowseq=17;} video;
 struct {int sequenceNumber=23;} audio;
};
void retire(session_t& session) {CLOSE}
video::packet_t make_video(session_t& session) {
 auto packet=std::make_unique<video::packet_impl>();packet->channel_data=CHANNEL;return packet;
}
audio::packet_t make_audio(session_t& session) {return {CHANNEL,{1,2,3}};}
int consume_video(video::packet_t packet, std::promise<void>* admitted=nullptr,
                  std::shared_future<void> release={}, bool fail=false) {
 for(int once=0;once<1;++once) {
 VIDEO_ADMIT
 if(admitted) {admitted->set_value();release.wait();}
 if(fail) throw std::runtime_error("send failed");
 VIDEO_READ
 return lowseq;
 }return -1;
}
int consume_audio(audio::packet_t value, std::promise<void>* admitted=nullptr,
                  std::shared_future<void> release={}) {
 auto* packet=&value;
 for(int once=0;once<1;++once) {
 AUDIO_ADMIT
 if(admitted) {admitted->set_value();release.wait();}
 AUDIO_READ
 return sequenceNumber;
 }return -1;
}
void replacements(bool release_encoder) {
 auto packet=std::make_unique<video::packet_impl>();
 auto owner=std::make_unique<video::encoder_t>();auto& session=*owner;
 auto old=std::make_unique<std::string>(256,'a');
 auto next=std::make_unique<std::string>(256,'b');
 REPLACEMENT_APPEND
 REPLACEMENT_PUBLISH
 if(release_encoder) owner.reset();else {old.reset();next.reset();}
 assert(packet->replacements->at(0).old[200]=='a');
 assert(packet->replacements->at(0)._new[200]=='b');
}
int main(int argc,char** argv) {
 assert(argc==2);std::string mode=argv[1];
 if(mode=="encoder-vector"||mode=="encoder-nal") {replacements(mode=="encoder-vector");}
 else if(mode=="queued-video") {
  safe::queue_t<video::packet_t> queue;
  auto session=std::make_unique<session_t>();queue.raise(make_video(*session));
  retire(*session);session.reset();assert(consume_video(queue.pop())==-1);
 } else if(mode=="queued-audio") {
  safe::queue_t<audio::packet_t> queue;
  auto session=std::make_unique<session_t>();queue.raise(make_audio(*session));
  retire(*session);session.reset();assert(consume_audio(std::move(*queue.pop()))==-1);
 } else if(mode=="active-video"||mode=="active-audio") {
  auto session=std::make_unique<session_t>();
  std::promise<void> entered, release;auto release_future=release.get_future().share();
  auto reader=std::async(std::launch::async,[&] {
   return mode=="active-video"?consume_video(make_video(*session),&entered,release_future):
                              consume_audio(make_audio(*session),&entered,release_future);
  });
  entered.get_future().wait();std::promise<void> closing;
  auto closer=std::async(std::launch::async,[&] {closing.set_value();retire(*session);session.reset();});
  closing.get_future().wait();
  bool blocked=closer.wait_for(30ms)==std::future_status::timeout;
  session_t other;assert(consume_video(make_video(other))==17);assert(consume_audio(make_audio(other))==23);
  release.set_value();assert(reader.get()==(mode=="active-video"?17:23));closer.get();assert(blocked);
 } else if(mode=="popped-before-close") {
  auto session=std::make_unique<session_t>();safe::queue_t<video::packet_t> queue;
  queue.raise(make_video(*session));auto popped=queue.pop();
  retire(*session);session.reset();assert(consume_video(std::move(popped))==-1);
 } else if(mode=="exception-exit") {
  session_t session;try {consume_video(make_video(session),nullptr,{},true);assert(false);}
  catch(const std::runtime_error&) {}retire(session);
 } else if(mode=="destructor-fence") {
  auto session=std::make_unique<session_t>();auto packet=make_video(*session);
  session.reset();assert(consume_video(std::move(packet))==-1);
 } else if(mode=="two-readers") {
  session_t session;std::promise<void> a,b,release;auto ready=release.get_future().share();
  auto v=std::async(std::launch::async,[&]{return consume_video(make_video(session),&a,ready);});
  auto s=std::async(std::launch::async,[&]{return consume_audio(make_audio(session),&b,ready);});
  assert(a.get_future().wait_for(2s)==std::future_status::ready);
  assert(b.get_future().wait_for(2s)==std::future_status::ready);
  release.set_value();assert(v.get()==17);assert(s.get()==23);retire(session);retire(session);
 } else if(mode=="overflow-coalesce-stop") {
  session_t a,b;safe::queue_t<video::packet_t> queue(2);
  for(int i=0;i<100;++i)queue.raise(make_video(a));
  auto replacement=make_video(b);auto key=replacement->channel_data;
  queue.raise_latest(std::move(replacement),[key](const video::packet_t& p){return p->channel_data==key;});
  queue.stop();retire(a);retire(b);queue.reset();
 } else if(mode=="address-reuse") {
  alignas(session_t) unsigned char storage[sizeof(session_t)];
  auto* first=new(storage)session_t();auto old=make_video(*first);retire(*first);first->~session_t();
  auto* second=new(storage)session_t();assert(consume_video(std::move(old))==-1);
  assert(consume_video(make_video(*second))==17);retire(*second);second->~session_t();
 } else if(mode=="null-channel") {
  assert(consume_video(std::make_unique<video::packet_impl>())==-1);
  assert(consume_audio(audio::packet_t{})==-1);
 } else {return 2;}
 std::cout<<"PASS "<<mode<<'\n';
}
'''
append = ('session.replacements->emplace_back' if 'shared_ptr' in replacement_decl
          else 'session.replacements.emplace_back') + '(std::string_view(*old),std::string_view(*next));'
tokens = {'CHANNEL_INCLUDE': '#include "src/stream_packet.h"' if guarded else '',
          'CHANNEL_FIELD': re.search(r'    packet_channel_t packet_channel =[^\n]*;', stream).group().replace('packet_channel_t', 'stream::packet_channel_t', 1).replace('make_shared<packet_channel_state_t>', 'make_shared<stream::packet_channel_state_t>') if guarded else '',
          'CHANNEL_DESTRUCTOR': block(stream, '~session_t()') if guarded else '',
          'PACKET_BASE': packet_base, 'REPLACEMENT_DECL': replacement_decl,
          'REPLACEMENT_APPEND': append, 'REPLACEMENT_PUBLISH': replacement_publish,
          'AUDIO_TYPE': audio_type, 'VIDEO_ADMIT': video_admit, 'AUDIO_ADMIT': audio_admit,
          'VIDEO_READ': re.search(r'      auto lowseq =[^\n]*;', video_loop).group(),
          'AUDIO_READ': re.search(r'      auto sequenceNumber =[^\n]*;', audio_loop).group(),
          'CLOSE': close, 'CHANNEL': channel}
for key in sorted(tokens, key=len, reverse=True):
    prefix = prefix.replace(key, tokens[key])
with tempfile.TemporaryDirectory() as temp:
    cpp = Path(temp)/'test.cpp';cpp.write_text(prefix);exe=Path(temp)/'test'
    cmd=['g++','-std=c++20','-g','-pthread','-fsanitize=address,undefined','-fno-omit-frame-pointer',
         '-I'+str(ROOT),'-I'+os.environ.get('AUDIT_JSON_INCLUDE','/usr/include'),str(cpp),'-o',str(exe)]
    subprocess.run(cmd, check=True)
    failed=0
    modes=['queued-video','queued-audio','active-video','active-audio','encoder-vector','encoder-nal',
           'two-readers','overflow-coalesce-stop','address-reuse','null-channel',
           'popped-before-close','exception-exit','destructor-fence']
    for mode in modes:
        result=subprocess.run([str(exe),mode],env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'),timeout=10)
        failed+=result.returncode!=0
    print(f'{len(modes)-failed}/{len(modes)} packet lifetime cases passed',flush=True)
    raise SystemExit(bool(failed))
