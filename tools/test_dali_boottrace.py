#!/usr/bin/env python3
"""Exercise the actual bounded boot-task exit helper without a kernel or phone."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0161-sweet-bounded-boot-task-exit-trace.patch'


class BootTaskTraceTest(unittest.TestCase):
    def test_actual_helper_is_opt_in_selected_bounded_and_read_only(self):
        lines = PATCH.read_text(encoding='latin-1').splitlines(keepends=True)
        additions = ''.join(line[1:] for line in lines if line.startswith('+') and not line.startswith('+++'))
        start = additions.index('static void\nsweet_boot_task_exit(')
        end = additions.index('\n#endif', start)
        helper = additions[start:end]
        fixture = '''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
typedef uint32_t UInt32;
struct process { char p_comm[17]; int pid; };
typedef struct process *proc_t;
static unsigned int enabled, outputs;
static int captured_status;
static bool PE_parse_boot_argn(const char *name, void *p, int size) {
    assert(!strcmp(name,"sweettasktrace") && size==4);
    memcpy(p,&enabled,4);return enabled!=0;
}
static UInt32 OSAddAtomic(int value, volatile UInt32 *p) {
    return __atomic_fetch_add(p,(UInt32)value,__ATOMIC_RELAXED);
}
static bool OSCompareAndSwap(UInt32 old, UInt32 value, volatile UInt32 *p) {
    return __atomic_compare_exchange_n(p,&old,value,0,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST);
}
static int proc_getpid(proc_t p) { return p->pid; }
static int trace_output(const char *format, ...) {
    va_list ap;va_start(ap,format);
    const char *phase=va_arg(ap,const char *),*name=va_arg(ap,const char *);
    int pid=va_arg(ap,int);captured_status=(int)va_arg(ap,unsigned int);
    unsigned int event=va_arg(ap,unsigned int);va_end(ap);
    assert(strstr(format,"SWEET BOOTTASK") && phase && name && pid==42);
    assert(event==outputs+1 && event<=64);++outputs;return 0;
}
#define printf trace_output
''' + helper + '''
#undef printf
int main(void) {
    struct process p={"init_featureflag",42};
    sweet_boot_task_exit(&p,"exit-request",0x500);assert(!outputs);
    enabled=2;sweet_boot_task_exit(&p,"exit-request",0);assert(!outputs);
    enabled=1;strcpy(p.p_comm,"not-selected");sweet_boot_task_exit(&p,"exit-request",0);assert(!outputs);
    strcpy(p.p_comm,"init_featureflag");struct process before=p;
    sweet_boot_task_exit(&p,"exit-request",0x500);assert(outputs==1 && captured_status==0x500);
    assert(!strcmp(p.p_comm,before.p_comm) && p.pid==before.pid);
    for(unsigned int i=1;i<80;++i)sweet_boot_task_exit(&p,"exit-final",0);
    assert(outputs==64);puts("PASS");return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, binary = root / 'trace.c', root / 'trace'
            source.write_text(fixture)
            compiler = shutil.which('clang') or shutil.which('cc')
            self.assertIsNotNone(compiler)
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'PASS')

    def test_heartbeat_can_skip_process_sampling(self):
        text = PATCH.read_text(encoding='latin-1')
        self.assertIn("sweet_sample_names[0] == '-' && heartbeat != 1", text)
        self.assertIn("sweet_sample_names[0] != '-'", text)
        self.assertIn('SWEET BOOTHEART: sample=%d', text)
        self.assertIn('"exit-request", rv', text)
        self.assertIn('"exit-final", exitval', text)


if __name__ == '__main__':
    unittest.main()
