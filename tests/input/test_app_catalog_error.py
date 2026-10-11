#!/usr/bin/env python3
"""Exercise the production catalog-entry error handler with malformed JSON.

The UUID/name reads and catch block are extracted from process.cpp. The rest of
app construction is omitted: the regression is an exception escaping recovery,
before the catalog can continue to the next entry. --baseline reads HEAD.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/process.cpp'], cwd=ROOT,
                                  text=True, encoding='utf-8')
          if '--baseline' in sys.argv else (ROOT / 'src/process.cpp').read_text(encoding='utf-8'))
start = source.index('        for (auto &app_node : tree["apps"]) {')
end = source.index('        fail_count = 0;', start)
loop = source[start:end]
uuid_read = re.search(r'ctx\.uuid = app_node\.at\("uuid"\);', loop).group()
name_read = re.search(r'std::string name = parse_env_val\(this_env, app_node\.value\("name", ""\)\);', loop).group()
handler = loop[loop.index('catch (const std::exception &e)', loop.index('apps.emplace_back')):]
# Exclude the final brace of the enclosing app loop, retaining the catch block.
handler = handler[:handler.rfind('}')]

code = r'''
#include <nlohmann/json.hpp>
#include <iostream>
#include <set>
#include <sstream>
#include <string>
#include <vector>
std::ostringstream logs;
#define BOOST_LOG(level) logs
std::string parse_env_val(int, const std::string &value) { return value; }
struct context { std::string uuid; };
int main() {
    using nlohmann::json;
    const json good = {{"uuid", "good"}, {"name", "Good"}};
    const std::vector<json> bad_entries = {
        {{"uuid", "bad"}, {"name", 7}},
        {{"uuid", "bad"}, {"name", nullptr}},
        7, nullptr, json::array(),
        {{"uuid", 7}, {"name", "Bad UUID"}},
        {{"name", "Missing UUID"}}
    };
    int checks = 0, failures = 0;
    for (const auto &bad : bad_entries) {
        json tree = {{"apps", json::array({good, bad, good})}};
        std::vector<int> retained;
        std::set<std::string> active_app_uuids;
        int i = 0, this_env = 0;
        bool escaped = false;
        try {
            for (auto &app_node : tree["apps"]) {
                std::string entry_uuid;
                try {
                    context ctx;
                    UUID_READ
                    entry_uuid = ctx.uuid;
                    NAME_READ
                    active_app_uuids.insert(ctx.uuid);
                    retained.push_back(i++);
                } HANDLER
            }
        } catch (const std::exception &) {
            escaped = true;
        }
        // A skipped entry that still carries a uuid keeps its app-id aliases alive.
        const bool bad_has_uuid = bad.is_object() && bad.contains("uuid") && bad["uuid"].is_string();
        const bool aliases_kept = !bad_has_uuid || active_app_uuids.count(bad["uuid"].get<std::string>()) == 1;
        const bool ok = !escaped && retained == std::vector<int>({0, 2}) && i == 3 && aliases_kept;
        ++checks;
        failures += !ok;
        std::cout << (ok ? "PASS " : "FAIL ") << "catalog continues around " << bad.dump() << '\n';
    }
    std::cout << checks << " checks; " << failures << " failures\n";
    return failures != 0;
}
'''
code = code.replace('UUID_READ', uuid_read).replace('NAME_READ', name_read).replace('HANDLER', handler)
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code, encoding='utf-8')
    command = ['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    if os.environ.get('AUDIT_JSON_INCLUDE'):
        command += ['-I', os.environ['AUDIT_JSON_INCLUDE']]
    subprocess.run(command + [str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    result = subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'), timeout=30)
    raise SystemExit(result.returncode != 0)
