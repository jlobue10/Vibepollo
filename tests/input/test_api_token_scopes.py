#!/usr/bin/env python3
"""Run production API token admission, restore, and authorization with in-memory storage."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
            if '--baseline' in sys.argv else (ROOT / path).read_text())
source, header = read('src/http_auth.cpp'), read('src/http_auth.h')
def block(text, marker):
    start = text.index(marker)
    end = text.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (text[end] == '{') - (text[end] == '}')
    return text[start:end + 1]

prefix = r'''
#define SUNSHINE_HTTP_AUTH_POLICY_ONLY
#include <boost/algorithm/string.hpp>
#include <boost/function.hpp>
#include <boost/property_tree/ptree.hpp>
#include <nlohmann/json.hpp>
#include <chrono>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <ranges>
#include <regex>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
namespace pt = boost::property_tree;
namespace confighttp {
std::string authentication_state_path() { return "memory"; }
struct InvalidScopeException : std::runtime_error { using std::runtime_error::runtime_error; };
TYPES
class ApiTokenManager {
public:
    explicit ApiTokenManager(const ApiTokenManagerDependencies &d) : _dependencies(d) {}
    bool authenticate_token(const std::string &, const std::string &, const std::string &);
    std::optional<std::string> create_api_token(const nlohmann::json &, const std::string &);
    std::optional<std::string> generate_api_token(const std::string &, const std::string &);
    std::optional<std::map<std::string, std::set<std::string, std::less<>>, std::less<>>> parse_json_scopes(const nlohmann::json &) const;
    std::optional<std::pair<std::string, std::set<std::string, std::less<>>>> parse_scope(const pt::ptree &) const;
    std::map<std::string, std::set<std::string, std::less<>>, std::less<>> build_scope_map(const pt::ptree &) const;
    void load_api_tokens();
    void save_api_tokens() { ++saves; }
    ApiTokenManagerDependencies _dependencies;
    std::mutex _mutex;
    std::map<std::string, ApiTokenInfo, std::less<>> _api_tokens;
    int saves = 0;
};
HELPER
METHODS
}
'''
tests = r'''
int main() {
    using namespace confighttp;
    using nlohmann::json;
    int checks = 0, failures = 0, serial = 0;
    auto check = [&](bool ok, const char *name) { ++checks; failures += !ok; std::cout << (ok ? "PASS " : "FAIL ") << name << '\n'; };
    pt::ptree saved;
    ApiTokenManagerDependencies deps;
    deps.now = [] { return std::chrono::system_clock::now(); };
    deps.rand_alphabet = [&](std::size_t) { return "token-" + std::to_string(++serial); };
    deps.hash = [](const std::string &s) { return s; };
    deps.file_exists = [](const std::string &) { return true; };
    deps.read_json = [&](const std::string &, pt::ptree &root) { root = saved; };
    deps.write_json = [](const std::string &, const pt::ptree &) {};
    ApiTokenManager manager(deps);
    const std::vector<json> invalid = {
        nullptr,
        json::array({{{"path",7},{"methods",{"GET"}}}}),
        json::array({{{"path","/api/apps"},{"methods",{7}}}}),
        json::array({{{"path","["},{"methods",{"GET"}}}}),
        json::array({{{"path",""},{"methods",{"GET"}}}})
    };
    for (const auto &scopes : invalid) {
        bool threw = false, accepted = false;
        int before = manager.saves;
        try { accepted = manager.create_api_token(scopes, "admin").has_value(); }
        catch (const std::exception &) { threw = true; }
        check(!threw && !accepted && before == manager.saves, "invalid scope is rejected without throwing, saving, or issuing a token");
    }
    auto token = manager.create_api_token(json::array({{{"path","^/api/v[0-9]+/users/[0-9]+$"},{"methods",{"get"}}}}), "admin");
    check(token.has_value(), "a valid regex scope is accepted");
    check(token && manager.authenticate_token(*token,"/api/v2/users/123","GET"), "valid regex and case-insensitive method still authorize");
    check(token && !manager.authenticate_token(*token,"/api/v2/users/abc","GET") && !manager.authenticate_token(*token,"/api/v2/users/123","POST"), "wrong path and method are denied");
    auto empty = manager.create_api_token(json::array({{{"path","/api/apps"},{"methods",json::array()}}}), "admin");
    check(empty && !manager.authenticate_token(*empty,"/api/apps","GET"), "empty method lists remain valid but grant no access");

    pt::ptree tokens;
    for (const auto &[key, path] : std::vector<std::pair<std::string,std::string>>{{"broken","["},{"empty",""},{"healthy","/api/apps"}}) {
        pt::ptree item, scopes, scope, methods;
        item.put("hash",key); item.put("username","admin"); item.put("created_at",0);
        scope.put("path",path); methods.push_back({"",pt::ptree("GET")});
        scope.add_child("methods",methods); scopes.push_back({"",scope});
        item.add_child("scopes",scopes); tokens.push_back({"",item});
    }
    saved.add_child("root.api_tokens",tokens);
    manager.load_api_tokens();
    bool threw = false, authorized = false;
    try { authorized = manager.authenticate_token("broken","/api/apps","GET"); }
    catch (const std::exception &) { threw = true; }
    check(!threw && !authorized, "a persisted invalid regex fails closed without escaping authentication");
    check(!manager.authenticate_token("empty","","GET"), "empty persisted scopes grant no access");
    check(manager.authenticate_token("healthy","/api/apps","GET"), "a neighboring valid persisted token still works");
    std::cout << checks << " checks; " << failures << " failures\n";
    return failures != 0;
}
'''
types = []
for marker in ['  struct ApiTokenManagerDependencies', '  struct ApiTokenScope', '  struct ApiTokenInfo']:
    if marker in header:
        types.append(block(header, marker) + ';')
helper = (block(source, '    std::optional<std::vector<ApiTokenScope>> compile_api_token_scopes(')
          if 'compile_api_token_scopes(' in source else '')
markers = ['  bool ApiTokenManager::authenticate_token(',
           '  std::optional<std::string> ApiTokenManager::create_api_token(',
           '  std::optional<std::string> ApiTokenManager::generate_api_token(',
           '  std::optional<std::map<std::string, std::set<std::string, std::less<>>, std::less<>>>',
           '  std::optional<std::pair<std::string, std::set<std::string, std::less<>>>> ApiTokenManager::parse_scope(',
           '  std::map<std::string, std::set<std::string, std::less<>>, std::less<>> ApiTokenManager::build_scope_map(',
           '  void ApiTokenManager::load_api_tokens(']
code = prefix.replace('TYPES','\n'.join(types)).replace('HELPER',helper).replace('METHODS','\n'.join(block(source,m) for m in markers)) + tests
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work/'test.cpp').write_text(code)
    cmd = ['g++','-std=c++20','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer']
    if os.environ.get('AUDIT_JSON_INCLUDE'):
        cmd += ['-I',os.environ['AUDIT_JSON_INCLUDE']]
    subprocess.run(cmd+[str(work/'test.cpp'),'-o',str(work/'test')],check=True)
    raise SystemExit(subprocess.run([str(work/'test')],env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'),timeout=30).returncode)
