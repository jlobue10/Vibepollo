#!/usr/bin/env python3
"""Run the production impersonation helper and FailGuard with scripted Win32 calls.

The callback runs real C++ normal and exception exits; only the OS token operations
and logging are faked. --baseline extracts HEAD.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (subprocess.check_output(['git', 'show', 'HEAD:src/platform/windows/misc.cpp'], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / 'src/platform/windows/misc.cpp').read_text())
utility = (ROOT / 'src/utility.h').read_text()

def block(src, marker):
    start = src.index(marker)
    end = src.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (src[end] == '{') - (src[end] == '}')
    return src[start:end + 1]

code = r'''
#include <functional>
#include <iostream>
#include <stdexcept>
#include <system_error>
#include <utility>
using namespace std::literals;
using HANDLE = int;
static bool permit=true, impersonating=false, revert_ok=true;
static int reverted=0, callbacks=0, breaks=0, checks=0, failures=0;
bool ImpersonateLoggedOnUser(HANDLE) { impersonating=permit; return permit; }
bool RevertToSelf() { ++reverted; if(revert_ok) impersonating=false; return revert_ok; }
int GetLastError() { return 5; }
void DebugBreak() { ++breaks; }
struct logger { template<class T> logger &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) logger()
namespace util {
@@GUARD@@
}
@@HELPER@@
static void check(bool ok, const char *name) {
  ++checks; failures+=!ok; std::cout<<(ok?"PASS ":"FAIL ")<<name<<'\n';
}
int main() {
  auto ec=impersonate_current_user(1, [] { ++callbacks; check(impersonating,"callback executes under the user token"); });
  check(!ec && reverted==1 && !impersonating, "normal completion restores the thread token once");
  bool caught=false; reverted=0;
  try { impersonate_current_user(1, [] { ++callbacks; throw std::runtime_error("callback failure"); }); }
  catch (const std::runtime_error &e) { caught=std::string(e.what())=="callback failure"; }
  check(caught, "the original callback exception propagates");
  check(reverted==1 && !impersonating, "a throwing callback restores the token before the caller catches it");
  // Non-std exceptions must unwind the token as well.
  reverted=0; caught=false;
  try { impersonate_current_user(1, [] { throw 7; }); } catch(int value) { caught=value==7; }
  check(caught && reverted==1 && !impersonating, "every exception type restores the token");
  permit=false; reverted=callbacks=0;
  ec=impersonate_current_user(1, [] { ++callbacks; });
  check(ec==std::errc::permission_denied && callbacks==0 && reverted==0,
        "failed impersonation neither executes the callback nor reverts an unacquired token");
  permit=true; revert_ok=false; reverted=0;
  impersonate_current_user(1, [] {});
  check(reverted==1 && breaks==1, "a failed revert still reaches the existing fatal path");
  std::cout<<checks<<" checks; "<<failures<<" failures\n";
  return failures!=0;
}
'''
start = utility.index('  template<class T>\n  class FailGuard')
end = utility.index('\n  template<class T>\n  void append_struct', start)
code = code.replace('@@GUARD@@', utility[start:end])
code = code.replace('@@HELPER@@', block(source, '  std::error_code impersonate_current_user('))
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
