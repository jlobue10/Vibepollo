#!/usr/bin/env python3
"""Run config GET/PATCH with the real parser and native file adapter on Linux.

An unreadable file in a writable directory must not be treated as an empty
configuration. HTTP/authentication and runtime reload are boundary fakes. Real
chmod, reads and atomic replacements exercise the production storage adapter.
--baseline extracts HEAD. Run as an account subject to file permissions.
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
parser = read('src/config.cpp')
code = r'''
#include <algorithm>
#include <cctype>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <tuple>
#include <unordered_map>
#include <sys/stat.h>
#include "file_handler.h"
#include <nlohmann/json.hpp>
using namespace std::literals;
using json=nlohmann::json;
struct logger { template<class T> logger &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) logger()
#define KITTY_WHILE_LOOP(x,y,z) { x; while(y) z }
#define TUPLE_2D(a,b,expr) decltype(expr) a##_##b=expr; auto &a=std::get<0>(a##_##b); auto &b=std::get<1>(a##_##b)
#define SUNSHINE_PLATFORM "linux"
#define PROJECT_VERSION "regression"
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
void add_cors_headers(SimpleWeb::CaseInsensitiveMultimap &h) { h.emplace("Access-Control-Allow-Origin","https://host"); }
void send_response(resp_https_t r,const json &body) { r->code=200; r->body=body; }
void bad_request(resp_https_t r,req_https_t,const std::string &text) { r->code=400; r->body={{"error",text}}; }
namespace config {
  struct { std::string config_file; } sunshine;
  int applied=0,deferred=0;
  void apply_config_now() { ++applied; }
  void mark_deferred_reload() { ++deferred; }
  std::string normalize_config_key(std::string key) { return key; }
  std::mutex &config_file_mutex() { static std::mutex mutex; return mutex; }
@@PARSER@@
}
namespace confighttp {
  void normalize_adapter_config_patch(json &) {}
  bool can_hot_apply_during_session(const std::set<std::string> &) { return false; }
  bool has_active_stream_sessions() { return false; }
@@HTTP@@
}
static int checks=0,failures=0;
static void check(bool ok,const std::string &name) {
  ++checks; failures+=!ok; if(!ok)std::cout<<"FAIL "<<name<<'\n';
}
static resp_https_t invoke(bool patch) {
  auto r=std::make_shared<response_t>(); auto q=std::make_shared<request_t>();
  q->content<<R"({"min_log_level":2})";
  try {
    if(patch)confighttp::patchConfig(r,q); else confighttp::getConfig(r,q);
  } catch(const std::exception &e) {
    check(false,std::string("file failure escaped the HTTP handler: ")+e.what());
  }
  return r;
}
static void rejected(bool patch,const std::string &name) {
  config::applied=config::deferred=0;
  auto r=invoke(patch);
  check(r->code==500 && r->body.contains("status") && !r->body["status"].get<bool>(),name+" reports HTTP 500/status=false");
  check(config::applied==0 && config::deferred==0,name+" has no reload effects");
  check(r->headers.count("Content-Type") && r->headers.count("Access-Control-Allow-Origin") &&
        r->headers.count("X-Frame-Options") && r->headers.count("Content-Security-Policy"),name+" keeps response headers");
}
int main(int argc,char **argv) {
  const std::filesystem::path root=argv[1],path=root/"sunshine.conf";
  config::sunshine.config_file=path.string();
  const std::string original="encoder = software\nport = 47989\n";
  for(bool patch:{false,true}) {
    {std::ofstream out(path); out<<original;}
    chmod(path.c_str(),0000);
    // Refuse a vacuous permission test (for example unrestricted root on a developer machine).
    if(std::ifstream(path).is_open()) {
      std::cerr<<"Run this regression as an account subject to chmod permissions\n";
      return 2;
    }
    rejected(patch,patch?"unreadable PATCH":"unreadable GET");
    chmod(path.c_str(),0600);
    check(file_handler::read_file(path.c_str())==original,"unreadable config remains byte-for-byte intact");
    std::filesystem::remove(path);
    rejected(patch,patch?"missing PATCH":"missing GET");
    check(!std::filesystem::exists(path),"failed read does not create a replacement config");
    std::filesystem::remove(path);
    std::filesystem::create_directory(path);
    rejected(patch,patch?"directory PATCH":"directory GET");
    check(std::filesystem::is_directory(path),"failed read preserves the directory");
    std::filesystem::remove(path);
  }
  // A successful empty read is distinct from an open or read failure.
  {std::ofstream out(path);}
  auto r=invoke(false);
  check(r->code==200 && r->body.value("status",false),"empty config GET succeeds");
  r=invoke(true);
  check(r->code==200 && config::parse_config(file_handler::read_file(path.c_str()))["min_log_level"]=="2",
        "empty config PATCH succeeds");
  for(std::size_t size:{4095,4096,4097,12289}) {
    std::string text="sunshine_name = "+std::string(size,'x')+"\n"+original;
    {std::ofstream out(path);out<<text;}
    r=invoke(false);
    check(r->code==200 && r->body.value("sunshine_name","")==std::string(size,'x'),"full read across buffer boundary");
    r=invoke(true);
    const auto saved=config::parse_config(file_handler::read_file(path.c_str()));
    check(r->code==200 && saved.at("sunshine_name")==std::string(size,'x') && saved.at("encoder")=="software" &&
          saved.at("port")=="47989" && saved.at("min_log_level")=="2","PATCH preserves every unrelated setting");
  }
  std::size_t files=0;for(const auto &entry:std::filesystem::directory_iterator(root)){(void)entry;++files;}
  check(files==1,"no leaked temporary file after rejected or successful writes");
  std::cout<<checks<<" checks; "<<failures<<" failures\n";
  return failures!=0;
}
'''
code = code.replace('@@PARSER@@', parser[parser.index('  bool endline(char ch)'):parser.index('  void string_f(')])
code = code.replace('@@HTTP@@', '\n'.join(block(http, x) for x in [
    '  static bool valid_config_key(', '  static bool append_config_line(',
    '  void config_persistence_error(', '  void getConfig(', '  void patchConfig(']))
with tempfile.TemporaryDirectory(prefix='config-read-failure-') as temp:
    work = Path(temp)
    (work / 'files').mkdir()
    (work / 'test.cpp').write_text(code)
    # Replace only the logging dependency; compile the complete production native adapter.
    adapter = read('src/file_handler.cpp').replace('#include "logging.h"', '''
struct logger { template<class T> logger &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) logger()
''')
    (work / 'native.cpp').write_text(adapter)
    (work / 'file_handler.h').write_text(read('src/file_handler.h'))
    (work / 'core.cpp').write_text(read('src/file_handler_core.cpp'))
    flags = ['-I', os.environ['AUDIT_JSON_INCLUDE']] if 'AUDIT_JSON_INCLUDE' in os.environ else []
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    *flags, '-I', str(work), str(work / 'test.cpp'), str(work / 'native.cpp'), str(work / 'core.cpp'),
                    '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test'), str(work / 'files')], timeout=20,
                                    env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
