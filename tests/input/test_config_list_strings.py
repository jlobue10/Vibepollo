#!/usr/bin/env python3
"""Run the production config parser and command-list readers with quoted brackets.

JSON construction is only test input. The real config scanner and Boost command
readers must preserve strings, nested arrays, following options and comment lines.
--baseline extracts HEAD.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/config.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/config.cpp').read_text())


def block(marker):
    start = source.index(marker)
    end, depth = source.index('{', start), 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


code = r'''
#include <algorithm>
#include <functional>
#include <iostream>
#include <iterator>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>
#include <boost/property_tree/json_parser.hpp>
#include <nlohmann/json.hpp>
using namespace std::literals;
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) Log{}
#define KITTY_WHILE_LOOP(x,y,z) { x; while(y) z }
#define TUPLE_2D(a,b,expr) decltype(expr) a##_##b=expr; auto &a=std::get<0>(a##_##b); auto &b=std::get<1>(a##_##b)
namespace config {
std::string normalize_config_key(std::string key) { return key; }
struct prep_cmd_t { std::string do_cmd,undo_cmd; bool elevated; };
struct server_cmd_t { std::string name,cmd; bool elevated; };
@@PARSER@@
@@READERS@@
}
static int checks=0,failures=0;
static void check(bool ok,const std::string &name) {
  ++checks; failures+=!ok;
  if(!ok)std::cout<<"FAIL "<<name<<'\n';
}
int main() {
  using nlohmann::json;
  const std::vector<std::string> commands={
    "echo ]", "echo [", "echo [title]", "echo ] then [",
    R"(powershell -Command "Write-Output ']'")",
    R"(echo \"[)", R"(echo C:\Games\[Example]\)", "echo # [tag]"
  };
  for(const auto &command:commands) for(const auto &newline:{"\n","\r\n"}) {
    const json prep=json::array({{{"do",command},{"undo","echo undo ]"},{"elevated",false}}});
    const json server=json::array({{{"name","Action [1]"},{"cmd",command},{"elevated",true}}});
    const std::string text="global_prep_cmd = "+prep.dump()+newline+
                           "server_cmd = "+server.dump()+newline+
                           "encoder = software"+newline+"port = 47989";
    auto vars=config::parse_config(text);
    check(vars["global_prep_cmd"]==prep.dump(),"prep JSON is intact: "+command);
    check(vars["server_cmd"]==server.dump(),"server JSON is intact: "+command);
    check(vars["encoder"]=="software" && vars["port"]=="47989" && vars.size()==4,
          "quoted brackets do not consume following settings: "+command);
    std::vector<config::prep_cmd_t> prep_commands;
    std::vector<config::server_cmd_t> server_commands;
    config::list_prep_cmd_f(vars,"global_prep_cmd",prep_commands);
    config::list_server_cmd_f(vars,"server_cmd",server_commands);
    check(prep_commands.size()==1 && prep_commands[0].do_cmd==command && prep_commands[0].undo_cmd=="echo undo ]",
          "valid prep command is accepted: "+command);
    check(server_commands.size()==1 && server_commands[0].name=="Action [1]" && server_commands[0].cmd==command,
          "valid server command is accepted: "+command);
  }
  const std::string list=R"([{"name":"] [ \" \\","values":[1,[2,3]]},"[",[]])";
  const auto nested=config::parse_config("option = "+list+"\nport = 47989\n");
  check(nested.at("option")==list && nested.count("port"),"nested arrays and escaped quotes/backslashes stay intact");
  for(const auto &comment:{"# ] early close", "# [ unmatched open", "# \" quote and ] bracket"}) {
    const auto vars=config::parse_config(std::string("option = [\n  ")+comment+"\n  1, [2, 3]\n]\nport = 47989\n");
    const auto parsed=json::parse(vars.at("option"),nullptr,false);
    check(parsed==json::array({1,json::array({2,3})}) && vars.count("port"),
          std::string("comment punctuation does not change list boundaries: ")+comment);
  }
  for(const auto &value:{"[]","[one, two]","[[1,2],[3,4]]"}) {
    const auto vars=config::parse_config(std::string("option = ")+value);
    check(vars.at("option")==value,"legacy and EOF list boundary remains valid");
  }
  std::cout<<checks<<" checks; "<<failures<<" failures\n";
  return failures!=0;
}
'''
code = code.replace('@@PARSER@@', source[source.index('  bool endline(char ch)'):source.index('  void string_f(')])
code = code.replace('@@READERS@@', '\n'.join(block(marker) for marker in [
    '  void string_f(', '  void list_prep_cmd_f(', '  void list_server_cmd_f(']))
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code)
    flags = ['-I', os.environ['AUDIT_JSON_INCLUDE']] if 'AUDIT_JSON_INCLUDE' in os.environ else []
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    *flags, str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'), timeout=30).returncode)
