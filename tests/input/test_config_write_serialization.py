#!/usr/bin/env python3
"""Race production Playnite metadata refresh against Web UI config writers.

The real parser and handlers run on two threads against a synchronized in-memory
file. A controlled read pause exposes stale-snapshot replacement. HTTP, auth,
runtime reload and platform callbacks are boundary fakes. --baseline reads HEAD.
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


def block(source, marker):
    start = source.index(marker)
    end, depth = source.index('{', start), 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


http = read('src/confighttp.cpp')
config = read('src/config.cpp')
playnite = read('src/platform/windows/playnite_integration.cpp')
config_playnite = read('src/config_playnite.cpp')
code = r'''
#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <functional>
#include <future>
#include <format>
#include <iostream>
#include <iterator>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <shared_mutex>
#include <sstream>
#include <string>
#include <thread>
#include <tuple>
#include <unordered_map>
#include <vector>
#include <nlohmann/json.hpp>
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
  int code=0; json body;
  void write(SimpleWeb::StatusCode status,const std::string &text,const SimpleWeb::CaseInsensitiveMultimap &) {
    code=static_cast<int>(status); body=json::parse(text);
  }
};
struct request_t { std::stringstream content; std::string method="POST"; std::vector<std::string> path_match; };
using resp_https_t=std::shared_ptr<response_t>; using req_https_t=std::shared_ptr<request_t>;
bool validateContentType(resp_https_t,req_https_t,const char *) { return true; }
bool authenticate(resp_https_t,req_https_t) { return true; }
void print_req(req_https_t) {}
void add_cors_headers(SimpleWeb::CaseInsensitiveMultimap &) {}
void send_response(resp_https_t response,const json &body) { response->code=200; response->body=body; }
void bad_request(resp_https_t response,req_https_t,const std::string &message) { response->code=400; response->body={{"error",message}}; }
namespace config {
  struct { std::string config_file="sunshine.conf"; } sunshine;
  struct { std::string file_apps="apps.json"; } stream;
  std::atomic<int> applies=0;
  void apply_config_now();
  void mark_deferred_reload() {}
  std::string normalize_config_key(std::string key) { return key; }
@@MUTEX@@
@@PARSER@@
}
namespace file_handler {
  std::mutex storage_mutex; std::condition_variable ready;
  std::string disk,apps; bool paused=false,resume=false; thread_local bool pause_read=false;
  bool fail_config_write=false;
  std::string read_file(const char *path) {
    std::unique_lock lock(storage_mutex);
    if(std::string_view(path)=="apps.json")return apps;
    auto snapshot=disk;
    if(pause_read) {
      pause_read=false; paused=true; ready.notify_all();
      ready.wait(lock,[]{return resume;});
    }
    return snapshot;
  }
  int write_file(const char *path,const std::string &contents) {
    std::lock_guard lock(storage_mutex);
    if(fail_config_write && std::string_view(path)!="apps.json")return -1;
    (std::string_view(path)=="apps.json" ? apps : disk)=contents; return 0;
  }
}
namespace proc { void refresh(const std::string &,bool) {} }
namespace remote_session { enum class control_e { none }; }
#define _WIN32 1
namespace confighttp {
  void normalize_adapter_config_pair(json &) {}
  void normalize_adapter_config_patch(json &) {}
  void reconcile_vulkan_hdr_layer_from_body(const json &) {}
  bool can_hot_apply_during_session(const std::set<std::string> &) { return false; }
  bool has_active_stream_sessions() { return false; }
  remote_session::control_e configurable_remote_session(std::string_view) { return remote_session::control_e::none; }
@@HTTP@@
}
namespace platf::playnite {
  struct Category { std::string id,name; };
  struct Game { std::string id,name; };
  struct Plugin { std::string id,name; };
  std::string single_line(std::string value) { std::erase_if(value,[](char c){return c=='\r'||c=='\n';}); return value; }
@@PLAYNITE@@
}
namespace config {
  std::shared_mutex g_apply_gate; std::mutex g_apply_mutex;
  bool exercise_reload=false; thread_local bool background_reload=false;
  std::mutex schedule_mutex; std::condition_variable schedule;
  bool background_ready=false,web_ready=false;
  void apply_config_now() {
    ++applies;
    if(exercise_reload && !background_reload) {
      std::lock_guard lock(schedule_mutex); web_ready=true; schedule.notify_all();
    }
    // The exact production reload gate acquisition and Playnite apps lock. The
    // configuration application body is a boundary fake; its lock order is real.
@@APPLY_GATES@@
    if(exercise_reload && background_reload) {
      std::unique_lock lock(schedule_mutex); background_ready=true; schedule.notify_all();
      schedule.wait(lock,[]{return web_ready;});
    }
    if(exercise_reload) {
@@APPS_GATE@@
    }
  }
}
static int checks=0,failures=0;
static void check(bool ok,const std::string &label) { ++checks; if(!ok){++failures;std::cout<<"FAIL "<<label<<'\n';} }
static void refresh() { platf::playnite::refresh_config_id_name_fields({{"category","Renamed"}},{},{}); }
static resp_https_t edit(int method) {
  auto response=std::make_shared<response_t>(); auto request=std::make_shared<request_t>();
  if(method==2)request->content<<R"({"uuid":"fullscreen"})";
  else request->content<<R"({"min_log_level":2})";
  if(method==0)confighttp::saveConfig(response,request);
  else if(method==1)confighttp::patchConfig(response,request);
  else confighttp::deleteApp(response,request);
  return response;
}
int main() {
  for(int method=0;method<3;++method) for(int first=0;first<2;++first) {
    // POST does not read existing config, so pause only the background reader for it.
    if(method==0 && first==1)continue;
    file_handler::disk="min_log_level = 1\nkeep = original\nplaynite_fullscreen_entry_enabled = true\n"
                       "playnite_sync_categories = [{\"id\":\"category\",\"name\":\"Old\"}]\n";
    file_handler::apps=R"({"apps":[{"uuid":"fullscreen","playnite-fullscreen":true}]})";
    file_handler::paused=file_handler::resume=false;
    resp_https_t response;
    auto run_first=std::async(std::launch::async,[&] {
      file_handler::pause_read=true;
      if(first==0)refresh(); else response=edit(method);
    });
    {
      std::unique_lock lock(file_handler::storage_mutex);
      if(!file_handler::ready.wait_for(lock,5s,[]{return file_handler::paused;}))return 2;
    }
    std::promise<void> started; auto started_future=started.get_future();
    auto run_second=std::async(std::launch::async,[&] {
      started.set_value();
      if(first==0)response=edit(method); else refresh();
    });
    started_future.wait();
    // Without serialization the second writer completes over the paused snapshot.
    // With serialization it waits for that writer's transaction to finish.
    run_second.wait_for(250ms);
    {
      std::lock_guard lock(file_handler::storage_mutex); file_handler::resume=true;
    }
    file_handler::ready.notify_all(); run_first.get(); run_second.get();
    const auto saved=config::parse_config(file_handler::disk);
    const auto label=std::to_string(method)+"/"+std::to_string(first);
    check(response && response->code==200,"web operation succeeds "+label);
    if(method<2)check(saved.at("min_log_level")=="2","web save survives background rename "+label);
    else check(saved.at("playnite_fullscreen_entry_enabled")=="false","fullscreen disable survives background rename "+label);
    if(method==0)check(saved.size()==1,"background snapshot cannot restore settings removed by POST "+label);
    else {
      check(saved.at("keep")=="original","unrelated setting preserved "+label);
      check(json::parse(saved.at("playnite_sync_categories"))[0]["name"]=="Renamed","metadata refresh survives web save "+label);
    }
  }
  file_handler::disk="playnite_fullscreen_entry_enabled = true\n";
  file_handler::apps=R"({"apps":[{"uuid":"fullscreen","playnite-fullscreen":true}]})";
  file_handler::fail_config_write=true; config::applies=0;
  auto failed=edit(2);
  check(failed->code==500 && !failed->body.value("status",false) && !failed->body.contains("playniteFullscreenDisabled"),
        "failed fullscreen-flag write is reported as a config persistence error");
  check(failed->body.value("error",std::string()).find("application was removed")!=std::string::npos,
        "error identifies the already-committed catalog deletion");
  check(config::applies==0,"failed flag write does not reload the old flag and recreate the deleted entry");
  check(file_handler::disk=="playnite_fullscreen_entry_enabled = true\n","failed flag write preserves the existing config");
  file_handler::fail_config_write=false;
  // A deferred reload owns the apply gate while deleteApp owns apps.json.
  // Deletion must release the apps lock before asking for another config reload.
  file_handler::disk="playnite_fullscreen_entry_enabled = true\n";
  file_handler::apps=R"({"apps":[{"uuid":"fullscreen","playnite-fullscreen":true}]})";
  config::exercise_reload=true;
  auto background=std::async(std::launch::async,[]{config::background_reload=true; config::apply_config_now();});
  {
    std::unique_lock lock(config::schedule_mutex);
    if(!config::schedule.wait_for(lock,5s,[]{return config::background_ready;}))return 2;
  }
  auto deletion=std::async(std::launch::async,[]{return edit(2);});
  const bool completed=deletion.wait_for(1s)==std::future_status::ready;
  check(completed,"fullscreen deletion cannot deadlock with a deferred configuration reload");
  if(!completed) {
    std::cout<<checks<<" checks; "<<failures<<" failures"<<std::endl;
    std::_Exit(1); // Do not join the intentionally reproduced deadlocked baseline.
  }
  deletion.get(); background.get();
  std::cout<<checks<<" checks; "<<failures<<" failures\n";
  return failures!=0;
}
'''
mutex = block(config, '  std::mutex &config_file_mutex(') if '  std::mutex &config_file_mutex(' in config else ''
code = code.replace('@@MUTEX@@', mutex)
code = code.replace('@@PARSER@@', config[config.index('  bool endline(char ch)'):config.index('  void string_f(')])
code = code.replace('@@HTTP@@', '\n'.join(block(http, x) for x in [
    *(['  static bool valid_config_key('] if '  static bool valid_config_key(' in http else []),
    '  static bool append_config_line(', '  void config_persistence_error(', '  void app_persistence_error(',
    '  std::recursive_mutex &apps_file_mutex(', '  std::optional<size_t> find_app_index_by_uuid(',
    '  std::optional<size_t> resolve_app_index_token(', '  void saveConfig(', '  void patchConfig(', '  void deleteApp(']))
apply = block(config, '  void apply_config_now(')
code = code.replace('@@APPLY_GATES@@', apply[apply.index('    std::unique_lock'):apply.index('    try {')])
apply_playnite = block(config_playnite, '  void apply_playnite(')
code = code.replace('@@APPS_GATE@@', next(line for line in apply_playnite.splitlines() if 'apps_lock' in line))
# Skip the forward declaration when selecting the metadata refresh definition.
marker = '  static void refresh_config_id_name_fields('
code = code.replace('@@PLAYNITE@@', block(playnite[playnite.index(marker) + len(marker):], marker))
with tempfile.TemporaryDirectory(prefix='config-serialization-') as temp:
    work = Path(temp); (work / 'test.cpp').write_text(code)
    flags = ['-I', os.environ['AUDIT_JSON_INCLUDE']] if 'AUDIT_JSON_INCLUDE' in os.environ else []
    subprocess.run(['g++', '-std=c++20', '-g', '-pthread', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', *flags, str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], timeout=20,
                                    env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
