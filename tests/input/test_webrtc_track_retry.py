#!/usr/bin/env python3
"""Compile production track setup, offer and teardown with failed native operations.

Reuse the signaling test's boundary fakes, replacing its track-setup stub with the
real function and counted native track/source objects. No WebRTC runtime needed.
--baseline reads HEAD's production code.
"""
from pathlib import Path
import ast
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/webrtc_stream.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/webrtc_stream.cpp').read_text())


def block(marker):
    start = source.rindex(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


# Read the reusable boundary fake declarations without executing the other test.
module = ast.parse((ROOT / 'tests/input/test_webrtc_peer_lifetime.py').read_text())
code = next(ast.literal_eval(node.value) for node in module.body
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'code' for t in node.targets))
code = code[:code.index('@@FUNCTIONS@@')]
code = code.replace('struct lwrtc_peer { int live=1234; };', '''
struct lwrtc_peer { int live=1234; int audio=0,video=0; };
struct lwrtc_factory {};
struct lwrtc_audio_source {}; struct lwrtc_encoded_video_source {};
struct lwrtc_audio_track {}; struct lwrtc_video_track {};
int audio_tracks=0,video_tracks=0,audio_sources=0,video_sources=0;
std::string fail_track_stage;
struct RawAudioFrame {std::shared_ptr<std::vector<int16_t>> samples;int sample_rate=48000,channels=2,frames=0;};
struct EmptyQueue {std::size_t size(){return 0;} bool empty(){return true;} bool drop_oldest(){return false;}
 bool pop_latest(RawAudioFrame &){return false;}};
''')
code = code.replace('void lwrtc_audio_track_release(lwrtc_audio_track_t *) {}',
                    'void lwrtc_audio_track_release(lwrtc_audio_track_t *t) {if(t){delete t;--audio_tracks;}}')
code = code.replace('void lwrtc_video_track_release(lwrtc_video_track_t *) {}',
                    'void lwrtc_video_track_release(lwrtc_video_track_t *t) {if(t){delete t;--video_tracks;}}')
code = code.replace('bool has_remote_offer=false,has_local_answer=false;', '''
    bool has_remote_offer=false,has_local_answer=false,audio=true,video=true;
    std::string id="session";std::size_t audio_dropped=0,video_dropped=0;''')
code = code.replace('  std::string remote_offer_sdp', '''
  EmptyQueue raw_audio_frames,video_frames;
  struct {int width=1920,height=1080;} video_config;
  using time_point=std::chrono::steady_clock::time_point;
  struct {std::optional<time_point> anchor_capture,anchor_send,recovery_prefer_latest_until;time_point last_drift_reset;} video_pacing_state;
  std::optional<time_point> last_video_push,startup_keyframe_until,startup_keyframe_deadline,last_keyframe_request;
  bool needs_keyframe=false;
  std::string remote_offer_sdp''')
code = code.replace('bool attach_media_tracks(Session &) { return !attach_fails; }', '')
code += r'''
template<typename T,void(*Release)(T*)>std::shared_ptr<T>make_lwrtc_ptr(T *p){return std::shared_ptr<T>(p,Release);}
lwrtc_audio_source_t *lwrtc_audio_source_create(lwrtc_factory_t *) {
 if(fail_track_stage=="audio-source")return nullptr;++audio_sources;return new lwrtc_audio_source;
}
void lwrtc_audio_source_release(lwrtc_audio_source_t *p){if(p){--audio_sources;delete p;}}
int lwrtc_audio_source_push(lwrtc_audio_source_t *,const void *,int,int,size_t,size_t){return 1;}
lwrtc_audio_track_t *lwrtc_audio_track_create(lwrtc_factory_t *,lwrtc_audio_source_t *,const char *) {
 if(fail_track_stage=="audio-track")return nullptr;++audio_tracks;return new lwrtc_audio_track;
}
int lwrtc_peer_add_audio_track(lwrtc_peer_t *p,lwrtc_audio_track_t *,const char *) {
 if(fail_track_stage=="audio-add")return 0;++p->audio;return 1;
}
lwrtc_encoded_video_source_t *lwrtc_encoded_video_source_create(lwrtc_factory_t *,lwrtc_video_codec_t,int,int) {
 if(fail_track_stage=="video-source")return nullptr;++video_sources;return new lwrtc_encoded_video_source;
}
void lwrtc_encoded_video_source_release(lwrtc_encoded_video_source_t *p){if(p){--video_sources;delete p;}}
lwrtc_video_track_t *lwrtc_encoded_video_track_create(lwrtc_factory_t *,lwrtc_encoded_video_source_t *,const char *) {
 if(fail_track_stage=="video-track")return nullptr;++video_tracks;return new lwrtc_video_track;
}
int lwrtc_peer_add_video_track(lwrtc_peer_t *p,lwrtc_video_track_t *,const char *) {
 if(fail_track_stage=="video-add")return 0;++p->video;return 1;
}
lwrtc_video_codec_t session_codec_to_encoded(const Session::State &){return static_cast<lwrtc_video_codec_t>(0);}
void on_webrtc_encoder_rate_update(void *,uint32_t,uint32_t){}
void request_keyframe(const char *){}
void ensure_media_thread(){}
constexpr auto kWebrtcStartupKeyframeHold=1s,kWebrtcStartupKeyframeDeadline=5s;
'''
contexts = source[source.index('    struct SessionIceContext {'):source.index('    lwrtc_constraints_t *create_constraints();')]
functions = '\n'.join(block(marker) for marker in (
    '    bool attach_media_tracks(', '  bool set_local_answer(',
    '    void on_set_local_success(', '    void store_negotiation_error(',
    '    void on_set_local_failure(', '    void on_create_answer_success(',
    '    void on_create_answer_failure(', '    void on_set_remote_success(',
    '    void on_set_remote_failure(', '  bool close_session(', '  bool set_remote_offer(',
))
code = (code.replace('@@HEADER@@', '#include "webrtc_peer.h"')
        .replace('@@CONTEXTS@@', contexts).replace('@@PEER@@', 'std::shared_ptr<peer_t> peer;') + functions)
code += r'''
int main(int argc,char **argv) {
 assert(argc==2);std::string mode=argv[1];
 sessions.try_emplace("session");++active_sessions;
 auto &s=sessions.at("session");s.factory=std::make_shared<lwrtc_factory>();
 bool existing=mode.rfind("existing-",0)==0;
 fail_track_stage=existing ? mode.substr(9) : mode;
 if(existing)s.peer=std::make_shared<peer_t>(new lwrtc_peer);
 check(!set_remote_offer("session","sdp","offer"),"failed native setup rejects the first offer");
 check(pending.empty(),"failed setup cannot start SDP negotiation");
 fail_track_stage.clear();
 check(set_remote_offer("session","sdp","offer"),"retry after native failure is accepted");
 {auto call=s.peer->acquire();check(call.get()->audio==1,"retried peer has exactly one attached audio track");
  check(call.get()->video==1,"retried peer has exactly one attached video track");}
 drain();
 check(set_remote_offer("session","sdp","offer"),"a repeated offer remains accepted");drain();
 {auto call=s.peer->acquire();check(call.get()->audio==1&&call.get()->video==1,"repeated offers cannot duplicate tracks");}
 check(close_session("session"),"session teardown succeeds after retry");
 check(audio_tracks==0&&video_tracks==0&&audio_sources==0&&video_sources==0,"all native tracks and sources are released");
 std::cout<<mode<<": "<<checks<<" checks, "<<failures<<" failures\n";return failures?1:0;
}
'''
with tempfile.TemporaryDirectory(prefix='webrtc-tracks-') as directory:
    work = Path(directory)
    cpp, binary = work / 'test.cpp', work / 'test'
    cpp.write_text(code)
    subprocess.run(['c++', '-std=c++20', '-pthread', '-g', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-DSUNSHINE_ENABLE_WEBRTC',
                    '-I' + str(ROOT / 'src'), '-I' + str(ROOT / 'third-party/libwebrtc/include'),
                    str(cpp), '-o', str(binary)], check=True)
    failed = []
    modes = [prefix + stage for prefix in ['', 'existing-'] for stage in
             ['audio-source', 'audio-track', 'audio-add', 'video-source', 'video-track', 'video-add']]
    for mode in modes:
        run = subprocess.run([str(binary), mode], timeout=15, capture_output=True, text=True,
                             env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
        print(run.stdout, end='')
        if run.returncode:
            failed.append(mode)
            print(run.stderr[:2000])
    print(f'{len(modes)-len(failed)}/{len(modes)} track-retry scenarios passed; failed: {failed}')
    sys.exit(bool(failed))
