#!/usr/bin/env python3
"""Execute the production offer HTTP handler with real JSON parsing.

Authentication, HTTP responses and the signaling engine are boundary fakes.
--baseline reads HEAD; no server, network or native WebRTC library is used.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/confighttp.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/confighttp.cpp').read_text())
start = source.index('  void postWebRTCOffer(')
handler = source[start:source.index('\n  void postWebRTCIce(', start)]
code = r'''
#include <chrono>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>
#include <nlohmann/json.hpp>
using nlohmann::json;
struct response_t { int status=0;json body; };
struct request_t { std::vector<std::string> path_match={"", "session"};std::stringstream content; };
using resp_https_t=std::shared_ptr<response_t>;
using req_https_t=std::shared_ptr<request_t>;
bool authenticate(resp_https_t,req_https_t) { return true; }
bool check_content_type(resp_https_t,req_https_t,const char *) { return true; }
void bad_request(resp_https_t response,req_https_t,const std::string &error) {
  response->status=400;response->body={{"error",error}};
}
void send_response(resp_https_t response,const json &body) { response->status=200;response->body=body; }
namespace webrtc_stream {
  int offers=0,waits=0;
  std::string last_type,last_sdp,negotiation_error;
  bool session_exists=true,answer_ready=true;
  bool set_remote_offer(const std::string &,const std::string &sdp,const std::string &type) {
    ++offers;last_type=type;last_sdp=sdp;return true;
  }
  bool get_session(const std::string &) { return session_exists; }
  std::string get_negotiation_error(const std::string &) { return session_exists ? negotiation_error : std::string {}; }
  bool get_local_answer(const std::string &,std::string &sdp,std::string &type) {
    if(!session_exists||!answer_ready)return false;sdp="local-answer";type="answer";return true;
  }
  bool wait_for_local_answer(const std::string &,std::string &sdp,std::string &type,std::chrono::seconds) {
    ++waits;if(!session_exists||!answer_ready)return false;sdp="local-answer";type="answer";return true;
  }
}
@@HANDLER@@
int main() {
  int checks=0,failures=0;
  auto check=[&](bool ok,const char *text) {
    ++checks;if(!ok){++failures;std::cerr<<"FAIL "<<text<<'\n';}
  };
  auto invoke=[](const json &body) {
    auto response=std::make_shared<response_t>();auto request=std::make_shared<request_t>();
    request->content<<body.dump();postWebRTCOffer(response,request);return response;
  };
  // An answerer cannot accept answers, provisional answers or rollbacks. In
  // particular, the pinned native wrapper dereferences a rollback's absent
  // media description before libwebrtc can reject it as an invalid transition.
  for(const std::string &type : {std::string("rollback"),std::string("answer"),std::string("pranswer"),
                                std::string("bogus"),std::string(),std::string("offer\0junk",10)}) {
    const int before=webrtc_stream::offers,waits=webrtc_stream::waits;
    auto response=invoke({{"sdp",""},{"type",type}});
    check(response->status==400&&response->body.contains("error"),"unsupported SDP type gets HTTP 400");
    check(webrtc_stream::offers==before&&webrtc_stream::waits==waits,"unsupported type never enters signaling or waits for an answer");
  }
  for(bool explicit_type : {false,true}) {
    json body={{"sdp","browser-offer"}};if(explicit_type)body["type"]="offer";
    const int before=webrtc_stream::offers;
    auto response=invoke(body);
    check(response->status==200&&response->body.value("answer_ready",false)&&response->body["sdp"]=="local-answer",
          "explicit and default offer types still return the negotiated answer");
    check(webrtc_stream::offers==before+1&&webrtc_stream::last_type=="offer"&&webrtc_stream::last_sdp=="browser-offer",
          "valid SDP reaches signaling unchanged");
  }
  // A session deleted while the offer handler awaited the answer (the delete
  // route runs on the other signaling thread) must be reported as gone. The web
  // client stops polling on any error other than "Answer not ready".
  auto poll=[]{
    auto response=std::make_shared<response_t>();auto request=std::make_shared<request_t>();
    getWebRTCAnswer(response,request);return response;
  };
  webrtc_stream::answer_ready=false;
  {
    auto response=invoke({{"sdp","browser-offer"}});
    check(response->status==200&&response->body.value("status",false)&&!response->body.value("answer_ready",true),
          "an answer that is merely late is reported as not ready");
    auto polled=poll();
    check(polled->status==200&&polled->body["error"]=="Answer not ready","polling a live session without an answer keeps the client polling");
  }
  webrtc_stream::session_exists=false;
  {
    auto response=invoke({{"sdp","browser-offer"}});
    check(response->status==200&&response->body["error"]=="Session not found"&&!response->body.contains("answer_ready"),
          "an offer whose session was deleted during the wait reports the session gone");
    auto polled=poll();
    check(polled->status==200&&polled->body["error"]=="Session not found","polling a deleted session reports it gone instead of not ready");
  }
  webrtc_stream::session_exists=true;webrtc_stream::answer_ready=true;
  std::cout<<checks<<" offer-type checks, "<<failures<<" failures\n";
  return failures ? 1 : 0;
}
'''.replace('@@HANDLER@@', handler)
with tempfile.TemporaryDirectory(prefix='webrtc-offer-types-') as directory:
    work = Path(directory)
    cpp, binary = work / 'test.cpp', work / 'test'
    cpp.write_text(code)
    command = ['c++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    if os.environ.get('AUDIT_JSON_INCLUDE'):
        command += ['-I' + os.environ['AUDIT_JSON_INCLUDE']]
    subprocess.run(command + [str(cpp), '-o', str(binary)], check=True)
    raise SystemExit(subprocess.run([str(binary)], timeout=15,
                     env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
