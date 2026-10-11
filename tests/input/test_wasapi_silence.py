#!/usr/bin/env python3
"""Execute production WASAPI sample assembly with a scripted capture boundary.

Checks silence duration and retained samples without an audio endpoint. The
controlled clock advances only on simulated wait timeouts. --baseline reads HEAD.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'src/platform/windows/audio.cpp'
src = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
       if '--baseline' in sys.argv else (ROOT/PATH).read_text())

def function(marker):
    start = src.index(marker, src.index('class mic_wasapi_t'))
    end = src.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (src[end] == '{') - (src[end] == '}')
    return src[start:end+1]

setup_start = src.index('      REFERENCE_TIME default_latency =', src.index('class mic_wasapi_t'))
setup_end = src.index('      std::uint32_t frames;', setup_start)
setup = src[setup_start:setup_end]
code = r'''
#include <algorithm>
#include <cstdint>
#include <deque>
#include <iostream>
#include <vector>
using REFERENCE_TIME = long long;
#define FAILED(x) ((x) < 0)
enum class capture_e {ok,timeout,reinit,error,interrupted};
struct Storage {std::vector<float> v = std::vector<float>(24000);float *begin(){return v.data();}float *end(){return v.data()+v.size();}float &operator[](size_t i){return v[i];}};
struct Base {virtual capture_e sample(std::vector<float> &)=0;};
class Mic: public Base {
public:
    Storage sample_buf;
    float *sample_buf_pos=sample_buf.begin();
    int channels=2;
    std::uint32_t sample_rate_hz=48000;
    bool continuous_audio=true;
    REFERENCE_TIME default_latency_ms=10, elapsed=0;
    std::vector<REFERENCE_TIME> waits;
    std::deque<std::vector<float>> chunks;
    struct Client {int GetDevicePeriod(REFERENCE_TIME *p,void *){*p=100000;return 0;}} client;
    Client *audio_client=&client;
    void configure(std::uint32_t sample_rate, std::uint32_t frame_size, bool continuous) {
SETUP
    }
    capture_e _fill_buffer(REFERENCE_TIME wait=-1) {
        if(wait<0)wait=default_latency_ms;waits.push_back(wait);
        if(chunks.empty()){elapsed+=wait;return capture_e::timeout;}
        auto chunk=chunks.front();chunks.pop_front();
        std::copy(chunk.begin(),chunk.end(),sample_buf_pos);sample_buf_pos+=chunk.size();
        return capture_e::ok;
    }
SAMPLE
};
int main(){
    int checks=0,failures=0;
    auto check=[&](bool ok,const char *name){++checks;failures+=!ok;std::cout<<(ok?"PASS ":"FAIL ")<<name<<'\n';};
    for(int channels:{2,6,8})for(int duration:{5,10,20,60}){
        Mic m;m.channels=channels;m.configure(48000,duration*48,true);
        std::vector<float> out(duration*48*channels,1);
        for(int i=0;i<10;++i)check(m.sample(out)==capture_e::ok,"continuous timeout produces a frame");
        check(m.elapsed==duration*10,"silent audio duration matches elapsed capture time");
        check(std::all_of(out.begin(),out.end(),[](float x){return x==0;}),"timeout output is silence");
        check(m.sample_buf_pos==m.sample_buf.begin(),"whole silent frames leave no surplus samples");
    }
    Mic partial;partial.configure(48000,480,true);
    std::fill_n(partial.sample_buf_pos,480,1.0f);partial.sample_buf_pos+=480;
    std::vector<float> out(960);
    check(partial.sample(out)==capture_e::ok&&std::all_of(out.begin(),out.begin()+480,[](float x){return x==1;})&&
          std::all_of(out.begin()+480,out.end(),[](float x){return x==0;}),"partial captured audio is padded to one frame");
    check(partial.waits==std::vector<REFERENCE_TIME>{5}&&partial.sample_buf_pos==partial.sample_buf.begin(),
          "partial frame waits and pads only its missing duration");
    partial.chunks.push_back(std::vector<float>(960,2));partial.sample(out);
    check(std::all_of(out.begin(),out.end(),[](float x){return x==2;}),"resuming sound has no extra silence carried ahead of it");
    Mic active;active.configure(48000,480,true);active.chunks.push_back(std::vector<float>(1440,3));
    check(active.sample(out)==capture_e::ok&&active.sample_buf_pos-active.sample_buf.begin()==480,
          "real surplus samples remain queued without synthetic padding");
    active.chunks.push_back(std::vector<float>(480,4));active.sample(out);
    check(out[0]==3&&out[479]==3&&out[480]==4&&out[959]==4,"successive captures retain sample order");
    Mic normal;normal.configure(48000,240,false);
    check(normal.sample(out)==capture_e::timeout&&normal.elapsed==10&&normal.sample_buf_pos==normal.sample_buf.begin(),
          "non-continuous capture retains device-period timeouts without inserting silence");
    std::cout<<checks<<" checks, "<<failures<<" failures\n";return failures?1:0;
}
'''.replace('SETUP',setup).replace('SAMPLE',function('capture_e sample('))
with tempfile.TemporaryDirectory(prefix='wasapi-silence-') as temp:
    cpp,binary=Path(temp)/'test.cpp',Path(temp)/'test'
    cpp.write_text(code)
    subprocess.run(['c++','-std=c++20','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(cpp),'-o',str(binary)],check=True)
    sys.exit(subprocess.run([str(binary)],env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0')).returncode)
