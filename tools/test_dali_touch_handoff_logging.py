#!/usr/bin/env python3
"""Execute actual touch observer and policy with fake registry/IO, not hardware."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

if len(sys.argv) != 2:
    raise SystemExit('usage: test_dali_touch_handoff_logging.py MATERIALIZED_XNU')
xnu = Path(sys.argv[1])
source = (xnu / 'iokit/Kernel/arm/DaliTouchHandoff.cpp').read_text()
start = source.index('void dali_touch_handoff_observe(')
body = source[start:source.rindex('\n#endif')]
assert 'kprintf(' not in body and body.count('IOLog(') == 2
prefix = r'''
#include <cassert>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <DaliTouchHandoff.h>
struct OSObject { virtual ~OSObject() = default; };
struct OSData : OSObject {
 const void *data; unsigned len;
 OSData(const void *d,unsigned n):data(d),len(n){}
 const void *getBytesNoCopy(){return data;} unsigned getLength(){return len;}
};
struct IORegistryEntry {
 std::map<std::string,OSObject *> properties;
 OSObject *getProperty(const char *name){auto p=properties.find(name);return p==properties.end()?nullptr:p->second;}
};
#define OSDynamicCast(type, object) dynamic_cast<type *>(object)
static unsigned opens,reads,closes,logs;
static bool fail;
static char last_log[256];
struct ReadMaps {
 static bool open(void *,unsigned,unsigned long long){++opens;return !fail;}
 static bool read(void *,unsigned,unsigned,unsigned *value){++reads;*value=0;return true;}
 static void close(void *,unsigned){++closes;}
};
static void IOLog(const char *format,...) {
 ++logs;va_list args;va_start(args,format);int n=vsnprintf(last_log,sizeof(last_log),format,args);va_end(args);
 assert(n>0 && n<(int)sizeof(last_log));
}
'''
post = r'''
int main(){
 IORegistryEntry root;
 dali_touch_handoff_observe(nullptr);dali_touch_handoff_observe(&root);assert(!opens&&!logs);
 const char compatible[]="D22AP\0xiaomi,dali";uint32_t enabled=1;
 OSData c(compatible,sizeof(compatible)),e(&enabled,4);
 root.properties["compatible"]=&c;dali_touch_handoff_observe(&root);assert(!opens&&!logs);
 root.properties["dali-touch-handoff"]=&e;enabled=0;dali_touch_handoff_observe(&root);assert(!opens&&!logs);
 enabled=1;dali_touch_handoff_observe(&root);assert(opens==4&&reads==8&&closes==4&&logs==1);
 assert(strstr(last_log,"DALI TOUCH HANDOFF: pin81-84=")&&strstr(last_log,"ownership and touch unknown"));
 const char other[]="xiaomi,dali-not";OSData o(other,sizeof(other));root.properties["compatible"]=&o;
 dali_touch_handoff_observe(&root);assert(opens==4&&logs==1);
 root.properties["compatible"]=&c;fail=true;dali_touch_handoff_observe(&root);
 assert(opens==5&&reads==8&&closes==4&&logs==2&&strstr(last_log,"mapping/read failed"));
 puts("PASS: actual touch observer uses persistent IOLog, disabled/non-Dali zero IO and failure logging");
}
'''
with tempfile.TemporaryDirectory(prefix='dali-touch-logging-') as folder:
    directory = Path(folder)
    shutil.copyfile(xnu / 'iokit/Kernel/arm/DaliTouchHandoff.h', directory / 'DaliTouchHandoff.h')
    fixture = directory / 'actual.cpp'
    fixture.write_text(prefix + body + post)
    binary = directory / 'actual'
    compiler = shutil.which('clang++') or shutil.which('c++')
    subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-I', str(directory), str(fixture), '-o', str(binary)], check=True, timeout=60)
    subprocess.run([str(binary)], check=True, timeout=20)
