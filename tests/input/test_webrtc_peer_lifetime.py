#!/usr/bin/env python3
"""Compile production signaling callbacks, offer/ICE handlers and close_session.

Native WebRTC, capture, input and history are boundary fakes. Real threads and
ASan exercise native-call/teardown overlap and late asynchronous callbacks.
--baseline reads HEAD and reproduces freed-peer access in five separate cases,
and candidates handed to the peer before it has a remote description.
"""
from pathlib import Path
import os
import re
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


contexts = source[source.index('    struct SessionIceContext {'):source.index('    lwrtc_constraints_t *create_constraints();')]
peer_field = re.search(r'(?:lwrtc_peer_t \*peer = nullptr;|std::shared_ptr<peer_t> peer;)', block('    struct Session {')).group()
guarded = 'std::shared_ptr' in peer_field
code = r'''
#include <algorithm>
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <future>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <vector>
#include <libwebrtc_c.h>
@@HEADER@@
using namespace std::chrono_literals;
using namespace webrtc_stream;
struct logger { template<class T> logger &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) logger()
int checks=0,failures=0;
void check(bool ok,const char *name) { ++checks;if(!ok){++failures;std::cerr<<"FAIL "<<name<<'\n';} }
struct lwrtc_peer { int live=1234; };
struct lwrtc_constraints {};
std::atomic_int closes=0,releases=0,ice_calls=0,answer_calls=0,local_calls=0,offer_calls=0;
// libwebrtc refuses AddIceCandidate until a remote description is set: count the
// candidates handed over before any set-remote-description completion.
std::atomic_int remote_descriptions=0,ice_before_remote=0;
std::vector<std::function<void()>> pending;
bool synchronous=false,cross_thread=false,create_fails=false,attach_fails=false,constraints_fail=false;
std::string fail_stage;
std::string answer_sdp="sdp";
std::string pause_at;
std::promise<void> native_entered,native_release,teardown_entered;
bool signal_teardown=false;
void native_entry(const char *stage,lwrtc_peer_t *peer) {
  if(pause_at==stage) { native_entered.set_value();native_release.get_future().wait(); }
  // Model the C bridge's first access to peer->handle. A released wrapper is
  // really deleted, so an invalid call is caught by ASan, not a text assertion.
  assert(peer->live==1234);
}
void dispatch(std::function<void()> callback) {
  if(cross_thread) { std::thread worker(std::move(callback));worker.join(); }
  else if(synchronous)callback();
  else pending.push_back(std::move(callback));
}
void next_callback() { auto callback=std::move(pending.front());pending.erase(pending.begin());callback(); }
void drain() { while(!pending.empty())next_callback(); }
void lwrtc_peer_close(lwrtc_peer_t *peer) { assert(peer->live==1234);peer->live=0;++closes; }
void lwrtc_peer_release(lwrtc_peer_t *peer) { assert(peer->live==0);delete peer;++releases; }
void lwrtc_peer_add_candidate(lwrtc_peer_t *peer,const char *,int,const char *) {
  native_entry("ice",peer);if(remote_descriptions==0)++ice_before_remote;++ice_calls;
}
void lwrtc_peer_set_remote_description(lwrtc_peer_t *peer,const char *,const char *,lwrtc_ok_cb ok,lwrtc_err_cb err,void *user) {
  native_entry("offer",peer);++offer_calls;dispatch([=]{if(fail_stage=="offer")err(user,"offer failure");else{++remote_descriptions;ok(user);}});
}
void lwrtc_peer_create_answer(lwrtc_peer_t *peer,lwrtc_sdp_cb ok,lwrtc_err_cb err,lwrtc_constraints_t *,void *user) {
  native_entry("answer",peer);++answer_calls;auto sdp=answer_sdp;
  dispatch([=]{if(fail_stage=="answer")err(user,"answer failure");else ok(user,sdp.c_str(),"answer");});
}
void lwrtc_peer_set_local_description(lwrtc_peer_t *peer,const char *,const char *,lwrtc_ok_cb ok,lwrtc_err_cb err,void *user) {
  native_entry("local",peer);++local_calls;dispatch([=]{if(fail_stage=="local")err(user,"local failure");else ok(user);});
}
void lwrtc_peer_register_state_callback(lwrtc_peer_t *,lwrtc_peer_state_cb,void *) {}
void lwrtc_peer_register_data_channel(lwrtc_peer_t *,lwrtc_data_channel_cb,void *) {}
void lwrtc_encoded_video_source_set_keyframe_callback(lwrtc_encoded_video_source_t *,lwrtc_keyframe_request_cb,void *) {}
void lwrtc_encoded_video_source_set_rate_callback(lwrtc_encoded_video_source_t *,lwrtc_rate_update_cb,void *) {}
void lwrtc_data_channel_unregister_observer(lwrtc_data_channel_t *) {}
void lwrtc_data_channel_close(lwrtc_data_channel_t *) {}
void lwrtc_data_channel_release(lwrtc_data_channel_t *) {}
void lwrtc_audio_track_release(lwrtc_audio_track_t *) {}
void lwrtc_video_track_release(lwrtc_video_track_t *) {}
void lwrtc_constraints_release(lwrtc_constraints_t *constraints) { delete constraints; }
lwrtc_constraints_t *create_constraints() { return constraints_fail ? nullptr : new lwrtc_constraints; }
namespace boost { bool iequals(const std::string &a,const std::string &b) { return a==b; } }
@@CONTEXTS@@
struct Av1FmtpParams { std::string profile,level_idx,tier; };
struct Session {
  struct State {
    bool has_remote_offer=false,has_local_answer=false;
    std::optional<std::string> codec;
    std::optional<bool> hdr;
    std::optional<int> audio_channels;
    std::size_t ice_candidates=0;
  } state;
  std::string remote_offer_sdp,remote_offer_type,local_answer_sdp,local_answer_type,negotiation_error;
  std::optional<Av1FmtpParams> av1_fmtp;
  std::optional<std::string> hevc_fmtp;
  @@PEER@@
  std::shared_ptr<lwrtc_factory_t> factory;
  std::shared_ptr<SessionIceContext> ice_context;
  std::shared_ptr<SessionDataChannelContext> data_channel_context;
  std::shared_ptr<SessionKeyframeContext> keyframe_context;
  std::shared_ptr<SessionRateContext> rate_context;
  std::shared_ptr<lwrtc_audio_source_t> audio_source;
  std::shared_ptr<lwrtc_video_source_t> video_source;
  std::shared_ptr<lwrtc_encoded_video_source_t> encoded_video_source;
  lwrtc_audio_track_t *audio_track=nullptr;
  lwrtc_video_track_t *video_track=nullptr;
  lwrtc_data_channel_t *input_channel=nullptr;
  struct IceCandidate { std::string mid;int mline_index;std::string candidate; };
  std::vector<IceCandidate> candidates;
  bool remote_description_applied=false;std::size_t candidates_applied=0;std::uint64_t offer_generation=0;
};
struct Av1Offer { bool offered=false;std::optional<Av1FmtpParams> fmtp; };
struct HevcOffer { bool offered=false;std::vector<std::string> fmtp_candidates; };
Av1Offer parse_av1_offer(const std::string &) { return {}; }
HevcOffer parse_hevc_offer(const std::string &) { return {}; }
bool hevc_offer_supports_main10(const std::string &) { return false; }
lwrtc_peer_t *create_peer_connection(Session &,SessionIceContext *) { return create_fails ? nullptr : new lwrtc_peer; }
bool attach_media_tracks(Session &) { return !attach_fails; }
void on_peer_state(void *,int) {}
void on_data_channel(void *,lwrtc_data_channel_t *) {}
std::string apply_opus_audio_params(const std::string &sdp,int) { return sdp; }
constexpr int kDefaultAudioChannels=2;
std::mutex session_mutex;
std::unordered_map<std::string,Session> sessions;
std::condition_variable local_answer_cv;
std::atomic_uint active_sessions=0,teardown_sessions=0;
std::atomic_bool rtsp_sessions_active=false;
namespace nvhttp { std::mutex gate;std::mutex &stream_lifecycle_mutex() { return gate; } }
namespace util {
 template<class F> struct guard { F f;~guard(){f();} };
 template<class F> auto fail_guard(F f) { return guard<F>{std::move(f)}; }
}
void reset_session_input(std::string_view) { if(signal_teardown)teardown_entered.set_value(); }
bool stop_webrtc_capture_if_idle_with_lifecycle_lock(std::unique_lock<std::mutex> &,bool *reserved) {
  if(*reserved){--teardown_sessions;*reserved=false;}return true;
}
std::optional<int> aggregate_webrtc_encoder_bitrate_locked() { return {}; }
void publish_webrtc_encoder_bitrate(int) {}
namespace host_stats { void webrtc_session_ended() {} }
namespace session_history { void end_session(const std::string &) {} }
namespace rtsp_stream { bool has_pending_launch_or_startup(){return false;}int session_count_no_cleanup(){return 0;} }
namespace stream::session { std::atomic_int running_sessions=0,teardown_sessions=0; }
namespace proc { struct process {void pause(bool){}} proc; }
bool has_active_or_pending_sessions() { return active_sessions!=0; }
@@FUNCTIONS@@
void start() { sessions.try_emplace("session");++active_sessions; }
bool offer() { return set_remote_offer("session","sdp","offer"); }
bool candidate() { return add_ice_candidate("session","video",0,"candidate"); }
int main(int argc,char **argv) {
  assert(argc==2);std::string mode=argv[1];start();
  if(mode=="normal" || mode=="sync" || mode=="cross-thread" || mode.rfind("error-",0)==0) {
    synchronous=mode=="sync";cross_thread=mode=="cross-thread";
    if(mode.rfind("error-",0)==0)fail_stage=mode.substr(6);
    check(candidate(),"ICE can queue before peer creation");
    check(offer(),"offer accepted");drain();
    check(ice_calls==(fail_stage=="offer" ? 0 : 1),"queued ICE replayed once, only with a remote description");
    check(ice_before_remote==0,"no candidate reaches the peer before its remote description is set");
    if(fail_stage.empty()) {
      check(sessions.at("session").state.has_local_answer,"answer stored");
      check(answer_calls==1 && local_calls==1,"full negotiation completes");
    } else check(!sessions.at("session").negotiation_error.empty(),"negotiation error recorded");
    check(candidate(),"active peer accepts ICE");
    check(close_session("session"),"session closes");
    check(!candidate() && !offer(),"deleted session rejects signaling");
    check(!close_session("session"),"duplicate close harmless");
  } else if(mode=="repeat-offer" || mode.rfind("repeat-error-",0)==0) {
    check(offer(),"first offer accepted");drain();
    std::string sdp,type;
    check(get_local_answer("session",sdp,type)&&sdp=="sdp","first answer can be polled");
    answer_sdp="replacement-sdp";
    if(mode.rfind("repeat-error-",0)==0)fail_stage=mode.substr(13);
    check(offer(),"replacement offer accepted");
    // A candidate for the new offer must not reach the peer while the old
    // remote description is still the one libwebrtc would match it against.
    const int ice_before_renegotiation=ice_calls;
    check(candidate(),"a candidate for the replacement offer is stored");
    check(ice_calls==ice_before_renegotiation,"a candidate during renegotiation is held until the new remote description is applied");
    check(!get_local_answer("session",sdp,type),"polling cannot return the previous offer's answer while renegotiating");
    check(!wait_for_local_answer("session",sdp,type,0ms),"the offer route waits for this negotiation instead of returning cached SDP");
    drain();
    check(ice_calls==ice_before_renegotiation+(fail_stage=="offer" ? 0 : 1),"the held candidate reaches the peer once the new remote description is applied, and only then");
    if(fail_stage.empty()) {
      check(wait_for_local_answer("session",sdp,type,0ms)&&sdp=="replacement-sdp","completed renegotiation returns the replacement answer");
    } else {
      check(!wait_for_local_answer("session",sdp,type,0ms),"a failed replacement cannot appear successful using the old answer");
      check(!get_local_answer("session",sdp,type)&&!sessions.at("session").negotiation_error.empty(),"polling reports no answer after a negotiation failure");
    }
    close_session("session");
  } else if(mode=="double-offer" || mode=="double-offer-stale-error") {
    // Two offers in flight on one peer: the completion of the replaced offer
    // must not mark the remote description applied, replay held candidates,
    // create an answer or (when it fails) record an error against the newer
    // negotiation. Only the newest offer's completion proceeds.
    if(mode=="double-offer-stale-error")fail_stage="offer";
    check(offer(),"first offer accepted");
    fail_stage.clear();
    check(candidate(),"a candidate is stored while the first offer is pending");
    check(offer(),"a second offer is accepted while the first is still pending");
    check(candidate(),"a candidate is stored while the second offer is pending");
    const int ice_before=ice_calls,answers_before=answer_calls;
    next_callback();
    check(ice_calls==ice_before&&answer_calls==answers_before,"the replaced offer's completion neither replays candidates nor creates an answer");
    check(!sessions.at("session").remote_description_applied,"the replaced offer's completion does not mark the remote description applied");
    check(sessions.at("session").negotiation_error.empty(),"a failure of the replaced offer is not recorded against the newer negotiation");
    std::string sdp,type;
    check(!wait_for_local_answer("session",sdp,type,0ms),"the offer route keeps waiting for the newer negotiation");
    drain();
    // Both stored candidates are handed over by the newest completion; libwebrtc
    // drops any whose ufrag belongs to the replaced description.
    check(sessions.at("session").remote_description_applied&&ice_calls==ice_before+2,"the newest offer's completion applies and replays the held candidates");
    check(answer_calls==answers_before+1&&local_calls==1,"exactly one answer is negotiated");
    check(wait_for_local_answer("session",sdp,type,0ms)&&sdp=="sdp","the newest negotiation's answer is returned");
    close_session("session");
  } else if(mode=="invalid-types-new" || mode=="invalid-types-active") {
    const bool active=mode=="invalid-types-active";
    if(active) { check(offer(),"initial offer accepted");drain(); }
    const auto old_offer=sessions.at("session").remote_offer_sdp;
    const auto old_answer=sessions.at("session").local_answer_sdp;
    const int calls_before=offer_calls;
    for(const std::string &type : {std::string("rollback"),std::string("answer"),std::string("pranswer"),
                                 std::string("bogus"),std::string(),std::string("offer\0junk",10)}) {
      check(!set_remote_offer("session","invalid-type-sdp",type),"only an offer can enter the answerer signaling flow");
      check(offer_calls==calls_before&&pending.empty(),"unsupported SDP types never reach the native peer");
      const auto &session=sessions.at("session");
      check(session.remote_offer_sdp==old_offer&&session.local_answer_sdp==old_answer&&
            session.state.has_remote_offer==active&&session.state.has_local_answer==active,
            "rejected SDP types preserve the current negotiation and cached answer");
      drain();
    }
    if(!active) { check(offer(),"a valid offer after rejected types still works");drain(); }
    close_session("session");
  } else if(mode=="create-failure" || mode=="attach-failure" || mode=="constraints-failure") {
    create_fails=mode=="create-failure";attach_fails=mode=="attach-failure";constraints_fail=mode=="constraints-failure";
    check(offer()==constraints_fail,"construction failure returned");drain();
    close_session("session");
  } else if(mode=="remote-callback" || mode=="answer-callback" || mode=="local-callback") {
    check(offer(),"offer accepted before close");
    if(mode!="remote-callback")next_callback();
    if(mode=="local-callback")next_callback();
    const int answers_before=answer_calls,locals_before=local_calls;
    check(close_session("session"),"close does not wait for queued asynchronous callbacks");
    drain();
    check(answer_calls==answers_before && local_calls==locals_before,"late callback cannot call the native peer");
  } else {
    // Pause inside the native boundary, after the production handler has
    // acquired its peer. Teardown must wait for this one admitted call.
    if(mode=="ice") { check(offer(),"initial offer accepted");drain(); }
    // The stored candidate is replayed from the set-remote-description completion;
    // run that completion inside the native call so the replay is the paused call.
    if(mode=="queued-ice") { check(candidate(),"initial candidate queued");synchronous=true; }
    pause_at=(mode=="offer") ? "offer" : "ice";
    auto request=std::async(std::launch::async,[&]{return mode=="ice" ? candidate() : offer();});
    check(native_entered.get_future().wait_for(2s)==std::future_status::ready,"native call entered");
    signal_teardown=true;
    auto teardown=std::async(std::launch::async,[]{return close_session("session");});
    check(teardown_entered.get_future().wait_for(2s)==std::future_status::ready,"teardown started");
    check(teardown.wait_for(50ms)==std::future_status::timeout,"teardown waits for admitted native call");
    native_release.set_value();check(request.get(),"admitted request finishes");
    check(teardown.get(),"teardown completes after the native call");
    signal_teardown=false;drain();
  }
  check(closes==(create_fails ? 0 : 1) && releases==closes,"native peer closed and released exactly once");
  check(sessions.empty() && active_sessions==0 && teardown_sessions==0,"session ownership fully retired");
  std::cout<<mode<<": "<<checks<<" checks, "<<failures<<" failures\n";
  return failures ? 1 : 0;
}
'''
functions = '\n'.join(block(marker) for marker in (
    '  bool set_local_answer(', '  bool get_local_answer(', '  bool wait_for_local_answer(',
    '    void on_set_local_success(', '    void store_negotiation_error(',
    '    void on_set_local_failure(', '    void on_create_answer_success(',
    '    void on_create_answer_failure(', '    void on_set_remote_success(',
    '    void on_set_remote_failure(', '  bool close_session(',
    '  bool set_remote_offer(', '  bool add_ice_candidate(',
))
code = (code.replace('@@HEADER@@', '#include "webrtc_peer.h"' if guarded else 'namespace webrtc_stream {}')
        .replace('@@CONTEXTS@@', contexts).replace('@@PEER@@', peer_field).replace('@@FUNCTIONS@@', functions))
with tempfile.TemporaryDirectory(prefix='webrtc-peer-') as directory:
    work = Path(directory)
    cpp, binary = work / 'test.cpp', work / 'test'
    cpp.write_text(code)
    subprocess.run(['c++', '-std=c++20', '-pthread', '-g', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-DSUNSHINE_ENABLE_WEBRTC',
                    '-I' + str(ROOT / 'src'), '-I' + str(ROOT / 'third-party/libwebrtc/include'),
                    str(cpp), '-o', str(binary)], check=True)
    failed = []
    modes = ['normal', 'sync', 'cross-thread', 'error-offer', 'error-answer', 'error-local',
                 'create-failure', 'attach-failure', 'constraints-failure',
                 'remote-callback', 'answer-callback', 'local-callback', 'ice', 'offer', 'queued-ice',
                 'repeat-offer', 'repeat-error-offer', 'repeat-error-answer', 'repeat-error-local',
                 'invalid-types-new', 'invalid-types-active', 'double-offer', 'double-offer-stale-error']
    for mode in modes:
        run = subprocess.run([str(binary), mode], timeout=15, capture_output=True, text=True,
                             env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
        print(run.stdout, end='')
        if run.returncode:
            failed.append(mode)
            print(run.stderr[:4500])
    print(f'{len(modes) - len(failed)}/{len(modes)} peer-lifetime scenarios passed; failed: {failed}')
    sys.exit(bool(failed))
