#!/usr/bin/env python3
"""Run production POST/PATCH config handlers, parser and atomic-write policy.

HTTP/authentication, adapter normalization, Vulkan registration and runtime reload
are boundary fakes. The filesystem fails either the temporary write or replacement.
The Windows-only callback is enabled after all system headers; this is not a
native Windows filesystem or Vulkan test. --baseline extracts HEAD.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
    return (ROOT / path).read_text()


source = read('src/confighttp.cpp')
parser = read('src/config.cpp')


def block(marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


code = r'''
#include <algorithm>
#include <cctype>
#include <functional>
#include <iostream>
#include <iterator>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <tuple>
#include <unordered_map>
#include <nlohmann/json.hpp>
#include "file_handler.h"
using namespace std::literals;
using json=nlohmann::json;
struct logger { template<class T> logger &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) logger()
#define KITTY_WHILE_LOOP(x,y,z) { x; while(y) z }
#define TUPLE_2D(a,b,expr) decltype(expr) a##_##b=expr; auto &a=std::get<0>(a##_##b); auto &b=std::get<1>(a##_##b)
namespace SimpleWeb {
  enum class StatusCode { server_error_internal_server_error=500 };
  using CaseInsensitiveMultimap=std::multimap<std::string,std::string>;
}
struct response_t {
  int code=0; json body; SimpleWeb::CaseInsensitiveMultimap headers;
  void write(SimpleWeb::StatusCode status,const std::string &text,const SimpleWeb::CaseInsensitiveMultimap &h) {
    code=static_cast<int>(status); body=json::parse(text); headers=h;
  }
};
struct request_t { std::stringstream content; };
using resp_https_t=std::shared_ptr<response_t>;
using req_https_t=std::shared_ptr<request_t>;
bool validateContentType(resp_https_t,req_https_t,const char *) { return true; }
bool authenticate(resp_https_t,req_https_t) { return true; }
void print_req(req_https_t) {}
void add_cors_headers(SimpleWeb::CaseInsensitiveMultimap &headers) { headers.emplace("Access-Control-Allow-Origin","https://host"); }
void send_response(resp_https_t response,const json &body) { response->code=200; response->body=body; }
void bad_request(resp_https_t response,req_https_t,const std::string &message) { response->code=400; response->body={{"error",message}}; }
namespace config {
  struct { std::string config_file="sunshine.conf"; } sunshine;
  int applied=0,deferred=0;
  void apply_config_now() { ++applied; }
  void mark_deferred_reload() { ++deferred; }
  std::string normalize_config_key(std::string key) { return key; }
  std::mutex &config_file_mutex() { static std::mutex mutex; return mutex; }
@@PARSER@@
}
struct storage_t: file_handler::filesystem_t {
  int fault=0,writes=0; std::string disk,staged;
  bool exists(const std::filesystem::path &) const override { return true; }
  bool create_directories(const std::filesystem::path &) override { return true; }
  std::string read(const std::filesystem::path &) const override { return disk; }
  bool write(const std::filesystem::path &,std::string_view contents) override {
    ++writes; if(fault==1)return false; staged=contents; return true;
  }
  file_handler::replace_result_e replace(const std::filesystem::path &,const std::filesystem::path &) override {
    if(fault==2)return file_handler::replace_result_e::error;
    disk=staged; return file_handler::replace_result_e::success;
  }
  std::optional<bool> read_only(const std::filesystem::path &) const override { return false; }
  bool set_read_only(const std::filesystem::path &,bool) override { return false; }
  void remove(const std::filesystem::path &) override { staged.clear(); }
  std::filesystem::path temporary_path(const std::filesystem::path &) override { return "config.tmp"; }
} storage;
namespace file_handler {
  handler_t handler(storage);
  std::string read_file(const char *path) { return handler.read_file(path); }
  std::optional<std::string> read_file_checked(const char *path) {
    if(storage.fault==3)return std::nullopt;
    return read_file(path);
  }
  int write_file(const char *path,const std::string_view &contents) { return handler.write_file(path,contents); }
}
// Enable only the handler's Windows side-effect branch, after all system headers.
#define _WIN32 1
namespace confighttp {
  int vulkan=0; bool active=false,hot=false;
  void normalize_adapter_config_pair(json &) {}
  void normalize_adapter_config_patch(json &) {}
  void reconcile_vulkan_hdr_layer_from_body(const json &) { ++vulkan; }
  bool can_hot_apply_during_session(const std::set<std::string> &) { return hot; }
  bool has_active_stream_sessions() { return active; }
@@HELPERS@@
@@HANDLERS@@
}
static int checks=0,failures=0;
static void check(bool ok,const std::string &name) {
  ++checks; failures+=!ok; if(!ok)std::cout<<"FAIL "<<name<<'\n';
}
static const std::string original="min_log_level = 1\nencoder = software\nremove_me = 1\n";
static void reset(int fault) {
  storage.fault=fault; storage.disk=original; storage.writes=0; storage.staged.clear();
  config::applied=config::deferred=confighttp::vulkan=0;
}
static resp_https_t invoke(int method,const json &body) {
  auto response=std::make_shared<response_t>(); auto request=std::make_shared<request_t>();
  request->content<<body.dump();
  if(method==0)confighttp::saveConfig(response,request);
  else confighttp::patchConfig(response,request);
  return response;
}
int main() {
  for(int method=0;method<2;++method) for(int policy=0;policy<4;++policy) for(int fault=0;fault<3;++fault) {
    reset(fault); confighttp::active=policy!=0; confighttp::hot=policy==1;
    json body={{"min_log_level",2},{"vulkan_hdr_layer",false},{"remove_me",nullptr}};
    if(policy==3)body["port"]=48000;
    auto response=invoke(method,body);
    const auto label=std::to_string(method)+"/"+std::to_string(policy)+"/"+std::to_string(fault);
    check(storage.writes==1,"one write "+label);
    if(fault) {
      check(response->code==500 && response->body.contains("status") && !response->body["status"].get<bool>(),
            "failed write returns HTTP 500/status=false "+label);
      check(config::applied==0 && config::deferred==0 && confighttp::vulkan==0,
            "failed write has no runtime or Vulkan effects "+label);
      check(storage.disk==original,"failed write preserves config "+label);
      check(response->headers.count("Content-Type") && response->headers.count("Access-Control-Allow-Origin") &&
            response->headers.count("Content-Security-Policy") && response->headers.count("X-Frame-Options"),
            "error keeps JSON/CORS/security headers "+label);
    } else {
      auto saved=config::parse_config(storage.disk);
      check(response->code==200 && response->body.value("status",false),"successful response "+label);
      check(saved["min_log_level"]=="2" && saved["vulkan_hdr_layer"]=="false" && !saved.count("remove_me"),
            "requested values persist "+label);
      check(saved.count("encoder")==static_cast<size_t>(method==1),"POST replaces/PATCH preserves other keys "+label);
      check(confighttp::vulkan==1 && config::applied==(policy<2) && config::deferred==(policy==2) &&
            response->body.value("appliedNow",false)==(policy<2) &&
            response->body.value("deferred",false)==(policy==2) &&
            response->body.value("restartRequired",false)==(policy==3),"reload policy unchanged "+label);
    }
  }
  for(int method=0;method<2;++method) {
    for(const json &body : {json(nullptr),json::array(),json::array({"value"}),json(true),json(7),json("value"),
                            json({{"min_log_level",2},{"bad\nkey",1}})}) {
      reset(0); auto response=invoke(method,body);
      check(response->code==400 && storage.writes==0 && storage.disk==original,
            "invalid shape/key rejects whole request before persistence "+std::to_string(method)+" "+body.dump());
      check(config::applied==0 && config::deferred==0 && confighttp::vulkan==0,
            "invalid request has no side effects "+std::to_string(method)+" "+body.dump());
    }
  }
  reset(3);
  auto unreadable=invoke(1,json({{"min_log_level",2}}));
  check(unreadable->code==500 && !unreadable->body.value("status",true),"PATCH reports a failed read as a storage error");
  check(storage.writes==0 && storage.disk==original,"failed read never writes a replacement config");
  check(config::applied==0 && config::deferred==0 && confighttp::vulkan==0,"failed read has no runtime or Vulkan effects");
  // A pre-existing non-identifier key on disk must not block a save (H30-01): it is dropped
  // with a warning, while a non-identifier key in the request is refused by name.
  for(int method=0;method<2;++method) {
    reset(0); storage.disk="min_log_level = 1\nbad key = 1\n";
    auto response=invoke(method,json({{"min_log_level",2}}));
    auto saved=config::parse_config(storage.disk);
    check(response->code==200 && storage.writes==1 && saved["min_log_level"]=="2" && !saved.count("bad key"),
          "a non-identifier key already on disk is dropped, not fatal "+std::to_string(method));
    reset(0); storage.disk="min_log_level = 1\nbad key = 1\n";
    response=invoke(method,json({{"bad key",1}}));
    check(response->code==400 && storage.writes==0 && response->body.value("error","").find("bad key")!=std::string::npos,
          "a non-identifier key in the request is refused by name "+std::to_string(method));
  }
  std::cout<<checks<<" checks; "<<failures<<" failures\n";
  return failures!=0;
}
'''
patch = block('  void patchConfig(')
if not ('valid_config_key(key)' in patch and patch.index('valid_config_key(key)') < patch.index('current[key] =')):
    print('FAIL patchConfig validates the request key before merging it')
    raise SystemExit(1)
helpers = ['  static bool append_config_line(']
if '  static bool valid_config_key(' in source:
    helpers.insert(0, '  static bool valid_config_key(')
if '  void config_persistence_error(' in source:
    helpers.append('  void config_persistence_error(')
code = code.replace('@@HELPERS@@', '\n'.join(block(x) for x in helpers))
code = code.replace('@@HANDLERS@@', '\n'.join(block('  void ' + x + '(') for x in ['saveConfig', 'patchConfig']))
code = code.replace('@@PARSER@@', parser[parser.index('  bool endline(char ch)'):parser.index('  void string_f(')])
with tempfile.TemporaryDirectory(prefix='config-persistence-') as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code)
    flags = ['-I', os.environ['AUDIT_JSON_INCLUDE']] if 'AUDIT_JSON_INCLUDE' in os.environ else []
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    *flags, '-I', str(ROOT / 'src'), str(work / 'test.cpp'), str(ROOT / 'src/file_handler_core.cpp'),
                    '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
