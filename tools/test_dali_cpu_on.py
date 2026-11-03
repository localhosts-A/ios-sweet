#!/usr/bin/env python3
"""Execute the actual startCPU body with a recorded, controllable PSCI stub."""
from pathlib import Path
import subprocess
import tempfile
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: test_dali_cpu_on.py MATERIALIZED_XNU_DIR')
src = (Path(sys.argv[1]) / 'iokit/Kernel/arm/SweetCPU.cpp').read_text()
start = src.index('kern_return_t\nSweetCPU::startCPU(')
end = src.index('\nvoid\nSweetCPU::quiesceCPU', start)
body = src[start:end]
pre = r'''
#include <cstdint>
#include <cstdio>
#include <stdexcept>
using vm_offset_t=uintptr_t; using kern_return_t=int;
constexpr int KERN_SUCCESS=0, DSB_SY=0;
#define IOLog(...) ((void)0)
#define panic(...) throw std::runtime_error("panic")
#define __builtin_arm_dsb(x) ((void)(x))
struct ml_topology_cpu_t {uint32_t phys_id;};
struct ml_topology_info_t {ml_topology_cpu_t cpus[2];};
static ml_topology_info_t topo;
static const ml_topology_info_t *ml_get_topology_info(){return &topo;}
extern "C" void sweet_secondary_entry(){}
static vm_offset_t ml_static_vtop(vm_offset_t p){return p;}
static int calls=0, retcode=0; static uint64_t target=0, entry=0, context=0;
static int32_t sweet_psci_cpu_on(uint64_t a,uint64_t b,uint64_t c){calls++;target=a;entry=b;context=c;return retcode;}
class SweetCPU {public: uint32_t number=1,initialized=0; unsigned getCPUNumber(){return number;} kern_return_t startCPU(vm_offset_t,vm_offset_t);};
'''
post = r'''
static void check(bool x){if(!x)throw std::runtime_error("assert");}
static bool rejected(SweetCPU &c,vm_offset_t a,vm_offset_t b){try{c.startCPU(a,b);return false;}catch(std::runtime_error&){return true;}}
int main(){SweetCPU c;topo.cpus[1].phys_id=0x100;check(c.startCPU(0,0)==KERN_SUCCESS);check(calls==1&&target==0x100&&context==1&&entry==(uintptr_t)&sweet_secondary_entry);
retcode=-3;check(rejected(c,0,0));check(calls==2);retcode=0;check(rejected(c,1,0));check(rejected(c,0,1));c.initialized=1;check(rejected(c,0,0));c.initialized=0;topo.cpus[1].phys_id=0;check(rejected(c,0,0));check(calls==2);
printf("actual SweetCPU::startCPU: PSCI target/entry/context, nonzero rc, invalid/restart PASS (mock firmware)\n");}
'''
with tempfile.TemporaryDirectory(prefix='dali-cpu-on-') as d:
    cpp, exe = Path(d)/'fixture.cpp', Path(d)/'fixture'
    cpp.write_text(pre+body+post)
    subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
