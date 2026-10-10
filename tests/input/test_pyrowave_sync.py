#!/usr/bin/env python3
"""Run production D3D11 fence setup/encode against the bundled PyroWave C API.

Fake D3D/Vulkan boundaries model handle ownership and an unsignalled conversion
timeline. No graphics driver is loaded. --baseline reads production code at HEAD.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
            if '--baseline' in sys.argv else (ROOT / path).read_text())
def block(source, marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]

core = read('src/platform/windows/pyrowave_d3d11_core.cpp')
api = read('third-party/pyrowave/pyrowave/pyrowave_c.cpp')
code = r'''
#include <array>
#include <cassert>
#include <chrono>
#include <cstring>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <vector>
#include <vulkan/vulkan.h>
#include "pyrowave.h"
#define private public
#include "src/platform/windows/pyrowave_d3d11_core.h"
#undef private
using HRESULT=int;using HANDLE=void*;
constexpr HRESULT S_OK=0;constexpr int D3D11_FENCE_FLAG_SHARED=1,GENERIC_ALL=1;
#define FAILED(x) ((x)<0)
#define IID_PPV_ARGS(x) (x)
static int created=0,closed=0,imports=0,failCreate=0,failShare=0,failImport=0;
static bool conversionReady=true,queueBlocked=false;static int submissions=0,blockedDestruction=0,mappedReads=0;
static std::vector<uint64_t> timeouts;
static void CloseHandle(HANDLE){++closed;}
struct ID3D11Fence {
 int id;
 HRESULT CreateSharedHandle(void*,int,void*,HANDLE* handle){
  if(failShare==id)return -1;*handle=reinterpret_cast<HANDLE>(uintptr_t(id));return 0;
 }
};
struct ID3D11Device5 {
 HRESULT CreateFence(uint64_t,int,ID3D11Fence** out){
  int id=++created;if(failCreate==id)return -1;*out=new ID3D11Fence{id};return 0;
 }
 HRESULT GetDeviceRemovedReason(){return conversionReady?0:-1;}
};
template<class T> struct ComPtr {
 T* value=nullptr;~ComPtr(){delete value;} T* operator->(){return value;}
 T** operator&(){return &value;}T* Get(){return value;}explicit operator bool()const{return value!=nullptr;}
};
struct ExternalHandle {HANDLE handle=nullptr;VkExternalSemaphoreHandleTypeFlagBits semaphore_handle_type;explicit operator bool()const{return handle!=nullptr;}};
struct FakeSemaphore {
 int id=0;
 bool import_from_handle(ExternalHandle ext){++imports;if(imports==failImport)return false;id=int(uintptr_t(ext.handle));CloseHandle(ext.handle);return true;}
};
using Semaphore=std::shared_ptr<FakeSemaphore>;
struct Device {
 struct Features {bool supports_external=true;} features;
 const Features& get_device_features(){return features;}
 Semaphore request_semaphore_external(VkSemaphoreType,VkExternalSemaphoreHandleTypeFlagBits){return std::make_shared<FakeSemaphore>();}
};
struct pyrowave_device_opaque {Device device;};
SYNC_STRUCT
namespace Util {static void set_thread_logging_interface(void*){}}
static int null_logger;
extern "C" {
SYNC_CREATE
void pyrowave_sync_object_destroy(pyrowave_sync_object sync){delete sync;}
VkSemaphore pyrowave_sync_object_get_semaphore(pyrowave_sync_object sync){return reinterpret_cast<VkSemaphore>(sync);}
pyrowave_result pyrowave_sync_object_cpu_wait(pyrowave_sync_object sync,uint64_t,uint64_t timeout){
 timeouts.push_back(timeout);
 // Imported fence 1 is the D3D conversion timeline; fence 2 is encode completion.
 bool complete=sync->semaphore->id==1?conversionReady:!queueBlocked;
 return complete?PYROWAVE_SUCCESS:PYROWAVE_TIMEOUT;
}
pyrowave_result pyrowave_encoder_encode_gpu_synchronous(pyrowave_encoder,const pyrowave_gpu_sync_operation* acquire,
 const pyrowave_gpu_sync_operation*,const pyrowave_gpu_buffers*,const pyrowave_rate_control*){
 ++submissions;queueBlocked=!conversionReady&&acquire&&acquire->sync.semaphore;return PYROWAVE_SUCCESS;
}
pyrowave_result pyrowave_encoder_get_mapped_raw_bitstream(pyrowave_encoder,const void** bitstream,size_t* bytes,const void** metadata,size_t* metadata_bytes){
 ++mappedReads;static uint64_t data=0;*bitstream=&data;*bytes=8;*metadata=nullptr;*metadata_bytes=0;return PYROWAVE_SUCCESS;
}
pyrowave_result pyrowave_encoder_compute_num_packets_with_padding(pyrowave_encoder,size_t,size_t,size_t* count){*count=1;return PYROWAVE_SUCCESS;}
pyrowave_result pyrowave_encoder_packetize_with_padding(pyrowave_encoder,pyrowave_packet* packets,size_t,size_t,size_t* count,void* data,size_t){
 *count=1;packets[0]={};packets[0].size=8;std::memset(data,0,8);return PYROWAVE_SUCCESS;
}
void pyrowave_encoder_destroy(pyrowave_encoder){blockedDestruction+=queueBlocked;}
void pyrowave_image_destroy(pyrowave_image){}
void pyrowave_device_destroy(pyrowave_device){blockedDestruction+=queueBlocked;}
const char* pyrowave_result_string(pyrowave_result){return "injected result";}
}
namespace pyrowave::d3d11 {
using clock_type=std::chrono::steady_clock;
static double elapsed_ms(clock_type::time_point a,clock_type::time_point b){return std::chrono::duration<double,std::milli>(b-a).count();}
static std::string hresult_string(HRESULT){return "removed";}
namespace protocol=pyrowave::protocol;
struct core_t::impl_t {
 ComPtr<ID3D11Device5> device11;
 ComPtr<ID3D11Fence> convert_fence,completion_fence;
 uint64_t convert_value=1,release_value=0;
 pyrowave_device pw_device=nullptr;pyrowave_encoder pw_encoder=nullptr;
 std::array<pyrowave_image,3> pw_planes{};
 std::array<pyrowave_gpu_external_reference,3> pw_plane_refs{};
 pyrowave_gpu_buffers pw_buffers{};
 pyrowave_sync_object pw_fence=nullptr,pw_release=nullptr;
 std::vector<pyrowave_packet> packets;std::vector<uint8_t> scratch;
 void error(const std::string&){}void warning(const std::string&){}
 result_e convert(const source_t&){return result_e::ok;}
 CREATE_FENCE
 DESTROY
};
core_t::core_t()=default;core_t::~core_t()=default;
ENCODE
}
int main(){
 using namespace pyrowave::d3d11;int failures=0,checks=0;
 auto check=[&](bool ok,const char* name){++checks;std::cout<<(ok?"PASS ":"FAIL ")<<name<<'\n';failures+=!ok;};
 auto reset=[] {created=closed=imports=failCreate=failShare=failImport=0;conversionReady=true;queueBlocked=false;submissions=blockedDestruction=mappedReads=0;timeouts.clear();};
 pyrowave_device_opaque device;
 reset();{
  core_t::impl_t state;state.device11.value=new ID3D11Device5;state.pw_device=&device;
  bool ok=state.create_fence();
  check(ok&&state.pw_fence&&state.pw_release,"both timelines initialize through the real bundled sync API");
  check(ok&&imports==2&&closed==2,"both shared handles are imported and consumed exactly once");
 }
 for(int kind=0;kind<3;kind++){
  reset();{
   core_t::impl_t state;state.device11.value=new ID3D11Device5;state.pw_device=&device;
   if(kind==0)failCreate=2;if(kind==1)failShare=2;if(kind==2)failImport=2;
   bool failed=!state.create_fence();
   check(failed&&state.pw_release==nullptr&&closed==(kind==2?2:1),
         kind==0?"completion fence allocation failure preserves conversion cleanup":kind==1?"completion handle failure leaves no unowned handle":"completion import failure closes its unconsumed handle");
  }
 }
 // Isolate encode ordering from the baseline startup defect by supplying two
 // known-good imported timelines. The unchanged bundled destructor waits idle.
 for(bool ready:{false,true}){
  reset();conversionReady=ready;{
   core_t c;c.impl=std::make_unique<core_t::impl_t>();auto& state=*c.impl;
   state.device11.value=new ID3D11Device5;state.pw_device=&device;state.pw_encoder=reinterpret_cast<pyrowave_encoder>(1);
   for(int id=1;id<=2;id++){
    pyrowave_sync_object_create_info info{};info.device=&device;info.external_handle=id;
    info.handle_type=VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_D3D12_FENCE_BIT;info.semaphore_type=VK_SEMAPHORE_TYPE_TIMELINE;
    assert(pyrowave_sync_object_create(&info,id==1?&state.pw_fence:&state.pw_release)==PYROWAVE_SUCCESS);
   }
   std::vector<uint8_t> out;framing_params_t framing;framing.framing=pyrowave::policy::framing_e::length_prefixed;
   auto result=c.encode({},1024,framing,out,nullptr);
   if(!ready){
    check(result==result_e::failed,"stalled conversion ends the encode attempt");
    check(submissions==0&&!queueBlocked,"stalled conversion never submits an unsatisfiable Vulkan wait");
    check(mappedReads==0,"stalled conversion never reads an incomplete bitstream");
   }else check(result==result_e::ok&&submissions==1&&mappedReads==1&&!out.empty(),"completed conversion reaches encoding and packetization");
   bool bounded=!timeouts.empty();for(auto t:timeouts)bounded&=t>0&&t<=2000000000ull;
   check(bounded,"all host sync waits carry a finite two-second bound");
  }
  check(blockedDestruction==0,"teardown has no pending external conversion dependency");
 }
 std::cout<<checks<<" checks, "<<failures<<" failures\n";return failures!=0;
}
'''.replace('SYNC_STRUCT', block(api, 'struct pyrowave_sync_object_opaque') + ';')
code = code.replace('SYNC_CREATE', block(api, 'pyrowave_result\npyrowave_sync_object_create('))
code = code.replace('CREATE_FENCE', block(core, '    bool create_fence()'))
code = code.replace('DESTROY', block(core, '    ~impl_t()'))
code = code.replace('ENCODE', block(core, '  result_e core_t::encode('))
with tempfile.TemporaryDirectory(prefix='pyrowave-sync-') as directory:
    work = Path(directory)
    (work / 'd3d11.h').write_text('#pragma once\nstruct ID3D11Device;struct ID3D11ShaderResourceView;\n')
    (work / 'dxgi.h').write_text('#pragma once\nstruct IDXGIKeyedMutex;struct IDXGIAdapter;struct LUID;\n')
    (work / 'test.cpp').write_text('#include "src/pyrowave_protocol.h"\n' + code)
    vendor = ROOT / 'third-party/pyrowave/pyrowave'
    command = ['g++', '-std=c++20', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
               '-I'+str(work), '-I'+str(ROOT), '-I'+str(vendor),
               '-I'+str(vendor/'Granite/third_party/khronos/vulkan-headers/include'),
               str(work/'test.cpp'), str(ROOT/'src/pyrowave_policy.cpp'), '-pthread', '-o', str(work/'test')]
    subprocess.run(command, check=True)
    raise SystemExit(subprocess.run([str(work/'test')], env=os.environ, timeout=15).returncode)
