#!/usr/bin/env python3
"""Compile and execute the actual patched SweetCPU::start body against IOKit stubs."""
import pathlib
import subprocess
import tempfile
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: test_dali_cpu_start.py MATERIALIZED_XNU_DIR')
src = (pathlib.Path(sys.argv[1]) / 'iokit/Kernel/arm/SweetCPU.cpp').read_text()
start = src.index('bool\nSweetCPU::start(IOService *provider)')
end = src.index('\n/* Do not wait in a nub', start)
body = src[start:end]
pre = r'''
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <map>
#include <string>
using namespace std;
#define MAX_CPUS 8
#define SWEET_SMP_MAX_CPUS 8
#define OSDynamicCast(T, x) dynamic_cast<T *>(x)
#define __builtin_arm_rsr64(x) ::live_mpidr
#define kprintf(...) ((void)0)
#define panic(...) throw runtime_error("panic")
struct OSObject { virtual ~OSObject() {} };
struct OSNumber: OSObject { uint32_t n; explicit OSNumber(uint32_t x):n(x){} uint32_t unsigned32BitValue(){return n;} };
struct OSData: OSObject { uint32_t n, len; OSData(uint32_t x, uint32_t l=4):n(x),len(l){} uint32_t getLength(){return len;} const void *getBytesNoCopy(){return &n;} };
struct IOService: OSObject { map<string,OSObject*> props; OSObject *getProperty(const char *p){return props[p];} };
struct OSSymbol { static const OSSymbol *withCStringNoCopy(const char*) { static OSSymbol x; return &x; } };
using kern_return_t=int; using thread_t=int; using wait_result_t=int; using cpu_id_t=void*;
using perfmon_interrupt_handler_func=void(*)();
constexpr int KERN_SUCCESS=0;
struct ml_topology_cpu_t { uint32_t cpu_id,phys_id,cluster_id,cluster_type,l2_cache_id,l2_cache_size,l3_cache_id,l3_cache_size; };
struct ml_topology_info_t { uint32_t num_cpus,max_cpu_id; ml_topology_cpu_t cpus[8]; ml_topology_cpu_t *boot_cpu; };
struct ml_processor_info_t { cpu_id_t cpu_id; uint32_t phys_id,log_id,cluster_id,cluster_type,l2_cache_id,l2_cache_size,l3_cache_id,l3_cache_size; };
static ml_topology_info_t topo;
static uint64_t live_mpidr;
static int registered=0, started_threads=0;
static const ml_topology_info_t *ml_get_topology_info(){return &topo;}
struct IOCPU { void *machProcessor=nullptr; void *ipi_handler=nullptr; bool start(IOService*){return true;} void setCPUNumber(unsigned){} void registerService(){} };
class SweetCPU: public IOCPU { public: const OSSymbol *cpuName=nullptr; bool daliCPU=false; uint32_t initialized=0; static void startAll(void*,wait_result_t){} bool start(IOService *); };
static SweetCPU *sweet_cpus[MAX_CPUS]; static uint32_t sweet_cpus_registered;
static int ml_processor_register(ml_processor_info_t *info, void **,void **,perfmon_interrupt_handler_func*) { if(info->phys_id != topo.cpus[info->log_id].phys_id) throw runtime_error("register id mismatch"); registered++; return 0; }
static int kernel_thread_start(void (*)(void*,wait_result_t),void*,thread_t *t){++started_threads; *t=1; return 0;}
static void thread_deallocate(thread_t){}
'''
post = r'''
static void require(bool v,const char* s){if(!v) throw runtime_error(s);}
static void init(unsigned count){memset(&topo,0,sizeof topo); topo.num_cpus=count; topo.max_cpu_id=count-1; for(unsigned i=0;i<count;i++){topo.cpus[i].cpu_id=i;topo.cpus[i].phys_id=i<<8;}topo.boot_cpu=&topo.cpus[0];live_mpidr=0; registered=started_threads=0;sweet_cpus_registered=0;memset(sweet_cpus,0,sizeof sweet_cpus);}
static bool run(unsigned idx,uint32_t reg,uint32_t flag=1,uint32_t flag_len=4,bool dali=true){SweetCPU *cpu=new SweetCPU; IOService p; OSNumber logical(idx); OSData r(reg),d(flag,flag_len);p.props["logical-cpu-id"]=&logical;p.props["reg"]=&r;if(dali)p.props["dali-boot-cpu"]=&d;return cpu->start(&p);}
int main(){
 init(1); require(run(0,0),"single boot");require(registered==1&&started_threads==1,"single registration");
 init(2); require(run(0,0)&&run(1,0x100),"dual registration");require(registered==2&&started_threads==1&&sweet_cpus_registered==2,"dual publication");
 init(2); require(!run(1,0x200),"bad reg accepted"); require(!run(1,0x100,0),"bad marker accepted"); require(!run(1,0x100,1,3),"bad marker size accepted");
 init(2); live_mpidr=0x100; bool caught=false;try{run(0,0);}catch(runtime_error&){caught=true;}require(caught,"bad boot MPIDR accepted");
 init(2); live_mpidr=0;require(run(1,0x100),"secondary matched on boot must not compare live MPIDR");
 init(8);for(unsigned i=0;i<8;i++)require(run(i,i<<8),"8 node structural registration");require(registered==8&&started_threads==1,"8 publication");
 init(2);require(run(0,0,1,4,false)&&run(1,0x100,1,4,false),"non-Dali registration");require(registered==2&&started_threads==1,"non-Dali publication");
 printf("actual SweetCPU::start: single, two, invalid reg/marker/boot, 8-node and non-Dali registration PASS\n");
}
'''
with tempfile.TemporaryDirectory(prefix='dali-cpu-start-') as d:
    cpp = pathlib.Path(d) / 'fixture.cpp'
    exe = pathlib.Path(d) / 'fixture'
    cpp.write_text(pre + body + post)
    subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],env={'ASAN_OPTIONS':'detect_leaks=0'},check=True)
