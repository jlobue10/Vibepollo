#!/usr/bin/env python3
"""Run the production output-log opener and startup-info builder with Win32/CRT fakes.

The fake process boundary enforces the documented HANDLE_LIST inheritance contract.
It does not execute a Windows process. --baseline extracts HEAD.
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
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


code = r'''
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <system_error>
using namespace std::literals;
using HANDLE=void*;
using DWORD=unsigned long;
constexpr int TRUE=1, FILE_APPEND_DATA=4, FILE_SHARE_READ=1, FILE_SHARE_WRITE=2,
  FILE_SHARE_DELETE=4, OPEN_ALWAYS=4, FILE_ATTRIBUTE_NORMAL=128,
  FILE_FLAG_OPEN_REPARSE_POINT=0x200000, SECURITY_SQOS_PRESENT=0x100000,
  SECURITY_ANONYMOUS=0, FILE_TYPE_DISK=1, FILE_ATTRIBUTE_REPARSE_POINT=1024,
  FILE_ATTRIBUTE_DIRECTORY=16, _O_APPEND=8, _O_WRONLY=1, STARTF_USESTDHANDLES=256,
  PROC_THREAD_ATTRIBUTE_HANDLE_LIST=1, PROC_THREAD_ATTRIBUTE_JOB_LIST=2;
#define INVALID_HANDLE_VALUE ((HANDLE)(intptr_t)-1)
struct SECURITY_ATTRIBUTES { DWORD nLength; void *lpSecurityDescriptor; int bInheritHandle; };
struct BY_HANDLE_FILE_INFORMATION { DWORD dwFileAttributes; };
struct STARTUPINFOEXW {
  struct { DWORD cb=0, dwFlags=0; HANDLE hStdInput=nullptr, hStdOutput=nullptr, hStdError=nullptr; } StartupInfo;
  void *lpAttributeList=nullptr;
};
struct fake_file {};
#define FILE fake_file
struct logger { template<class T> logger &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) logger()
struct { std::string output="log.txt"; } _app;
static int failure, closes, bridge_calls, fd_closes, handle_marker, list_count;
static DWORD attributes, desired_access, sharing, flags;
static bool inheritable, listed;
static HANDLE live_handle=&handle_marker;
static HANDLE CreateFileW(const wchar_t *,DWORD a,DWORD share,SECURITY_ATTRIBUTES *sa,DWORD disposition,DWORD f,HANDLE) {
  desired_access=a; sharing=share; flags=f;
  inheritable=sa && sa->nLength==sizeof(*sa) && sa->bInheritHandle;
  return failure==1 || disposition!=OPEN_ALWAYS ? INVALID_HANDLE_VALUE : live_handle;
}
static DWORD GetLastError() { return 5; }
static DWORD GetFileType(HANDLE) { return failure==2 ? 3 : FILE_TYPE_DISK; }
static bool GetFileInformationByHandle(HANDLE,BY_HANDLE_FILE_INFORMATION *info) {
  info->dwFileAttributes=attributes; return failure!=3;
}
static void CloseHandle(HANDLE h) { if(h==live_handle)++closes; }
// The CRT bridge takes ownership of the existing handle; it does not change its inheritance.
static int _open_osfhandle(intptr_t h,int) { ++bridge_calls; return failure==4 || (HANDLE)h!=live_handle ? -1 : 7; }
static FILE *_wfdopen(int,const wchar_t *) { return failure==5 ? nullptr : new FILE; }
static void _close(int) { ++fd_closes; CloseHandle(live_handle); }
static int _fileno(FILE *) { return 7; }
static intptr_t _get_osfhandle(int) { return (intptr_t)live_handle; }
struct closer { void operator()(FILE *file) const { if(file) { _close(7); delete file; } } };
static std::unique_ptr<FILE,closer> _pipe;
static void *allocate_proc_thread_attr_list(int) { return &handle_marker; }
static bool UpdateProcThreadAttribute(void *,int,int kind,void *value,std::size_t size,void *,void *) {
  if(kind==PROC_THREAD_ATTRIBUTE_HANDLE_LIST) {
    list_count=size/sizeof(HANDLE); listed=list_count==1 && *(HANDLE*)value==live_handle;
  }
  return true;
}
@@STARTUP@@
static void open_log_file() {
  const std::wstring woutput=L"log.txt";
  @@OPEN@@;
  open_log();
}
static int checks, failures;
static void check(bool ok,const char *name) {
  ++checks; failures+=!ok; std::cout<<(ok?"PASS ":"FAIL ")<<name<<'\n';
}
static void reset(int fault=0,DWORD attrs=0) {
  _pipe.reset(); failure=fault; attributes=attrs;
  closes=bridge_calls=fd_closes=list_count=0; inheritable=listed=false;
}
int main() {
  reset(); open_log_file();
  check(_pipe && bridge_calls==1 && closes==0,"plain disk log reaches the CRT with one owner");
  check(inheritable,"log handle is inheritable before startup-info construction");
  std::error_code ec;
  auto startup=create_startup_info(_pipe.get(),nullptr,ec);
  check(!ec && listed && list_count==1 && startup.StartupInfo.hStdOutput==live_handle &&
        startup.StartupInfo.hStdError==live_handle,"only the intended log is listed for stdout and stderr");
  // HANDLE_LIST restricts inheritance; it does not grant inheritance to an unmarked handle.
  check(inheritable && listed && (startup.StartupInfo.dwFlags & STARTF_USESTDHANDLES),
        "redirected launch satisfies the Windows handle-inheritance contract");
  check(desired_access==FILE_APPEND_DATA && sharing==(FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE) &&
        (flags & FILE_FLAG_OPEN_REPARSE_POINT) && (flags & SECURITY_SQOS_PRESENT),
        "append, sharing, reparse and impersonation restrictions remain in force");
  _pipe.reset(); check(closes==1 && fd_closes==1,"accepted log closes exactly once through its FILE owner");
  for(int fault=1;fault<=5;++fault) {
    reset(fault); open_log_file();
    check(!_pipe,"failed open, validation or CRT conversion leaves redirection disabled");
    check(closes==(fault==1?0:1),"each rejected acquired handle closes exactly once");
    check(fd_closes==(fault==5?1:0),"only failed fdopen transfers cleanup to the CRT");
  }
  for(DWORD attrs : {DWORD(FILE_ATTRIBUTE_REPARSE_POINT),DWORD(FILE_ATTRIBUTE_DIRECTORY)}) {
    reset(0,attrs); open_log_file();
    check(!_pipe && closes==1 && bridge_calls==0,"reparse points and directories are refused before CRT transfer");
  }
  reset(); startup=create_startup_info(nullptr,nullptr,ec);
  check(!(startup.StartupInfo.dwFlags & STARTF_USESTDHANDLES) && !listed,"no log means no inherited standard handles");
  std::cout<<checks<<" checks; "<<failures<<" failures\n";
  return failures!=0;
}
'''
code = code.replace('@@STARTUP@@', block(read('src/platform/windows/misc.cpp'), '  STARTUPINFOEXW create_startup_info('))
code = code.replace('@@OPEN@@', block(read('src/process.cpp'), 'const auto open_log = [&]()'))
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
