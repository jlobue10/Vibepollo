#!/usr/bin/env python3
"""Exercise production app handlers and file-write policy with failed storage.

The real JSON, save/reorder/delete handlers, cache helper and file_handler_core
run against a scripted filesystem; HTTP, authentication and process refresh
are boundary fakes. Windows-only artwork/live-HDR hooks are outside this fixture.
--baseline extracts HEAD.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/confighttp.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/confighttp.cpp').read_text())


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
#include <format>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <vector>
#include <nlohmann/json.hpp>
#include "file_handler.h"
using namespace std::literals;
using json=nlohmann::json;
struct logger { template<class T> logger &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) logger()
namespace SimpleWeb {
  enum class StatusCode { server_error_internal_server_error=500 };
  using CaseInsensitiveMultimap=std::multimap<std::string,std::string>;
}
using enum SimpleWeb::StatusCode;
struct response_t {
  int code=0; json body;
  void write(SimpleWeb::StatusCode status, const std::string &text, const SimpleWeb::CaseInsensitiveMultimap &) {
    code=static_cast<int>(status); body=json::parse(text);
  }
};
struct request_t { std::stringstream content; std::string method="POST"; std::vector<std::string> path_match; };
using resp_https_t=std::shared_ptr<response_t>;
using req_https_t=std::shared_ptr<request_t>;
bool validateContentType(resp_https_t,req_https_t,const char *) { return true; }
bool authenticate(resp_https_t,req_https_t) { return true; }
void print_req(req_https_t) {}
void add_cors_headers(SimpleWeb::CaseInsensitiveMultimap &) {}
void send_response(resp_https_t response,const json &body) { response->code=200; response->body=body; }
void bad_request(resp_https_t response,req_https_t,const std::string &message) { response->code=400; response->body={{"error",message}}; }
namespace config { struct { std::string file_apps="apps.json"; } stream; }
namespace remote_session {
  enum class control_e { none,input,monitor };
  struct synthetic_t { std::string uuid,title; };
  synthetic_t synthetic(control_e) { return {}; }
}
namespace uuid_util {
  struct uuid_t { static uuid_t generate() { return {}; } std::string string() const { return "generated"; } };
}
namespace proc {
  int refreshes=0;
  void refresh(const std::string &,bool) { ++refreshes; }
  void migrate_apps(json *,json *) {}
}
struct storage_t: file_handler::filesystem_t {
  int fault=0, writes=0; std::string disk, staged;
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
  std::filesystem::path temporary_path(const std::filesystem::path &) override { return "apps.tmp"; }
} storage;
namespace file_handler {
  handler_t handler(storage);
  std::string read_file(const char *path) { return handler.read_file(path); }
  int write_file(const char *path,const std::string_view &contents) { return handler.write_file(path,contents); }
}
namespace confighttp {
remote_session::control_e configurable_remote_session(std::string_view) { return remote_session::control_e::none; }
void normalize_adapter_config_pair(json &) {}
bool refresh_client_apps_cache(json &,bool sort_by_name=true);
@@HELPERS@@
@@HANDLERS@@
}
static int checks, failures;
static void check(bool ok,const char *name) {
  ++checks; failures+=!ok; std::cout<<(ok?"PASS ":"FAIL ")<<name<<'\n';
}
int main() {
  const std::string original=R"({"apps":[{"uuid":"a","name":"Alpha"},{"uuid":"b","name":"Beta"}]})";
  for (const std::string path : {"/home/games/Title: Subtitle", "/tmp/COM1.txt", "/tmp/NUL"}) {
    storage.fault=0; storage.disk=original; storage.writes=proc::refreshes=0;
    auto response=std::make_shared<response_t>(); auto request=std::make_shared<request_t>();
    request->content<<json({{"uuid","a"},{"name","Alpha"},{"output",path},{"working-dir",path}}).dump();
    confighttp::saveApp(response,request);
    check(response->code==200 && storage.writes==1 && proc::refreshes==1,
          "POSIX saves allow colons and Windows device names in both app paths");
    check(json::parse(storage.disk)["apps"][0].value("output","")==path &&
          json::parse(storage.disk)["apps"][0].value("working-dir","")==path,
          "POSIX paths survive catalog persistence unchanged");
  }
  for(int operation=0;operation<3;++operation) for(int fault=0;fault<3;++fault) {
    storage.fault=fault; storage.disk=original; storage.writes=proc::refreshes=0;
    auto response=std::make_shared<response_t>(); auto request=std::make_shared<request_t>();
    const char *body=operation==0 ? R"({"uuid":"a","name":"Updated"})" :
                     operation==1 ? R"({"order":["b","a"]})" : R"({"uuid":"a"})";
    request->content<<body;
    if(operation==0)confighttp::saveApp(response,request);
    else if(operation==1)confighttp::reorderApps(response,request);
    else confighttp::deleteApp(response,request);
    check(storage.writes==1,"each mutation attempts one atomic catalog write");
    if(fault) {
      check(response->code==500 && !response->body.value("status",false),
            "a failed catalog write is an HTTP error, never a reported success");
      check(proc::refreshes==0,"failed storage does not reload an unchanged catalog");
      check(storage.disk==original,"failed temporary write or replace preserves the existing catalog");
    } else {
      const auto apps=json::parse(storage.disk)["apps"];
      const auto updated=confighttp::find_app_index_by_uuid(apps,"a");
      check(response->code==200 && response->body.value("status",false) && proc::refreshes==1,
            "a successful mutation returns success and refreshes once");
      check(operation==0 ? updated && apps[*updated]["name"]=="Updated" :
            operation==1 ? apps[0]["uuid"]=="b" : apps.size()==1 && apps[0]["uuid"]=="b",
            "successful save, reorder and delete persist the requested change");
    }
  }
  storage.fault=1; storage.disk=original; proc::refreshes=0;
  auto tree=json::parse(original);
  check(!confighttp::refresh_client_apps_cache(tree) && proc::refreshes==0,
        "catalog-sync callers also receive a false result on storage failure");
  std::cout<<checks<<" checks; "<<failures<<" failures\n";
  return failures!=0;
}
'''
helpers = ['  static void sort_apps_by_name(', '  std::optional<size_t> find_app_index_by_uuid(',
           '  std::optional<size_t> resolve_app_index_token(', '  std::recursive_mutex &apps_file_mutex(',
           '  bool refresh_client_apps_cache(']
if '  void app_persistence_error(' in source:
    helpers.append('  void app_persistence_error(')
code = code.replace('@@HELPERS@@', '\n'.join(block(x) for x in helpers))
code = code.replace('@@HANDLERS@@', '\n'.join(block('  void '+x+'(') for x in ['saveApp','reorderApps','deleteApp']))
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code)
    flags = ['-I', os.environ['AUDIT_JSON_INCLUDE']] if 'AUDIT_JSON_INCLUDE' in os.environ else []
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    *flags, '-I', str(ROOT / 'src'), str(work / 'test.cpp'), str(ROOT / 'src/file_handler_core.cpp'),
                    '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
