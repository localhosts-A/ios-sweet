#!/usr/bin/env python3
"""Execute actual bounded Dali CPU activity reader with processor_info stubs."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

if len(sys.argv) != 2:
    raise SystemExit('usage: test_dali_cpu_activity.py MATERIALIZED_XNU')
source = (Path(sys.argv[1]) / 'iokit/Kernel/arm/SweetCPU.cpp').read_text()
start = source.index('void\nSweetCPU::dumpActivity(')
end = source.index('\nvoid\nSweetCPU::free(', start)
actual = source[start:end]
pre = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#define SWEET_SMP_MAX_CPUS 8
using kern_return_t = int;
using host_t = void *;
#define HOST_NULL nullptr
#define KERN_SUCCESS 0
using mach_msg_type_number_t = unsigned;
using processor_info_t = int *;
constexpr int PROCESSOR_BASIC_INFO=1, PROCESSOR_CPU_LOAD_INFO=2, PROCESSOR_CPU_STAT64=3;
constexpr unsigned PROCESSOR_BASIC_INFO_COUNT=5, PROCESSOR_CPU_LOAD_INFO_COUNT=4, PROCESSOR_CPU_STAT64_COUNT=20;
constexpr unsigned CPU_STATE_USER=0, CPU_STATE_SYSTEM=1, CPU_STATE_IDLE=2;
struct processor_basic_info_data_t { unsigned running; };
struct processor_cpu_load_info_data_t { unsigned cpu_ticks[4]; };
struct processor_cpu_stat64_data_t { uint64_t irq_ex_cnt, ipi_cnt, timer_cnt; };
struct ml_topology_info_t { unsigned num_cpus; };
static ml_topology_info_t topology;
static const ml_topology_info_t *ml_get_topology_info() { return &topology; }
struct Processor { unsigned id; };
class SweetCPU {
public:
 bool daliCPU=true;
 uint32_t initialized=1;
 Processor *machProcessor=nullptr;
 static void dumpActivity(unsigned);
};
static SweetCPU *sweet_cpus[8];
static unsigned queries, lines, failures;
static char output[256];
static void IOLog(const char *format, ...) {
 va_list args; va_start(args,format);
 int size=vsnprintf(output,sizeof(output),format,args); va_end(args);
 assert(size>0 && size<(int)sizeof(output)); ++lines;
}
static kern_return_t processor_info(Processor *p,int flavor,host_t *host,processor_info_t data,mach_msg_type_number_t *count) {
 assert(p && host && data && count); ++queries;
 if(failures && p->id==1) return 5;
 *host=(void *)1;
 if(flavor==PROCESSOR_BASIC_INFO){assert(*count==5);((processor_basic_info_data_t *)data)->running=1;}
 else if(flavor==PROCESSOR_CPU_LOAD_INFO){assert(*count==4);auto *q=(processor_cpu_load_info_data_t *)data;q->cpu_ticks[0]=p->id+4;q->cpu_ticks[1]=8;q->cpu_ticks[2]=10;}
 else {assert(flavor==PROCESSOR_CPU_STAT64 && *count==20);auto *q=(processor_cpu_stat64_data_t *)data;q->irq_ex_cnt=100;q->ipi_cnt=20;q->timer_cnt=80;}
 return 0;
}
'''
post = r'''
int main() {
 Processor processors[8]; SweetCPU objects[8];
 for(unsigned i=0;i<8;i++){processors[i].id=i;objects[i].machProcessor=&processors[i];sweet_cpus[i]=&objects[i];}
 topology.num_cpus=2;
 SweetCPU::dumpActivity(1); SweetCPU::dumpActivity(40); assert(!queries && !lines);
 objects[0].daliCPU=false;SweetCPU::dumpActivity(0);assert(!queries && !lines);objects[0].daliCPU=true;
 SweetCPU::dumpActivity(0);assert(queries==6 && lines==2 && strstr(output,"cpu=1 run=1 user=5"));
 objects[1].initialized=0;SweetCPU::dumpActivity(10);assert(queries==9 && lines==4 && strstr(output,"ready=0"));objects[1].initialized=1;
 failures=1;SweetCPU::dumpActivity(20);assert(queries==15 && lines==6 && strstr(output,"query=5,5,5"));failures=0;
 topology.num_cpus=8;sweet_cpu_activity_dump(30);assert(queries==39 && lines==14);
 topology.num_cpus=9;SweetCPU::dumpActivity(0);assert(queries==39 && lines==14);
 puts("PASS: actual activity reader, cadence, non-Dali, offline, query failures and eight CPUs");
}
'''
compiler = shutil.which('clang++') or shutil.which('c++')
with tempfile.TemporaryDirectory(prefix='dali-cpu-activity-') as directory:
    path = Path(directory)
    fixture = path / 'actual.cpp'
    fixture.write_text(pre + actual + post)
    binary = path / 'actual'
    subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', str(fixture), '-o', str(binary)], check=True, timeout=60)
    subprocess.run([str(binary)], check=True, timeout=20)
