#!/usr/bin/env python3
"""Run actual finite watchdog policy and HID startup/timer code without hardware."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0170-dali-finite-late-gui-startup-budgets.patch'


def fragment(path, patch=PATCH):
    for block in patch.read_text().split('diff --git ')[1:]:
        if f'b/{path}\n' in block:
            return ''.join(line[1:] for line in block.splitlines(keepends=True)
                           if line.startswith(('+', ' ')) and not line.startswith('+++'))
    raise AssertionError(path)


WDT = fragment('pexpert/arm/DaliWatchdogPolicy.h')
WAIT = fragment('iokit/Kernel/arm/hid/SweetHIDWaitPolicy.h')
HID = fragment('iokit/Kernel/arm/hid/SweetHID.cpp')
START = HID[HID.index('bool\nSweetHIDTouch::start('):HID.index('\nvoid\nSweetHIDTouch::swipeFired(')]
TIMER = HID[HID.index('void\nSweetHIDTouch::swipeFired('):HID.index('\nvoid\nSweetHIDTouch::touch(')]
HID_FIXTURE = r'''
typedef uint32_t UInt32;
class OSObject {};
class IOService : public OSObject {};
class IOTimerEventSource {
public:
    unsigned calls=0,last=0;
    void setTimeoutMS(unsigned value) { ++calls;last=value; }
    static IOTimerEventSource *timerEventSource(OSObject *,void (*)(OSObject *,IOTimerEventSource *));
};
static IOTimerEventSource timer;
static unsigned creations,locks,logs;
IOTimerEventSource *IOTimerEventSource::timerEventSource(OSObject *owner,void (*callback)(OSObject *,IOTimerEventSource *)) {
    assert(owner && callback);++creations;timer.calls=timer.last=0;return &timer;
}
class WorkLoop { public: int addEventSource(IOTimerEventSource *source) { assert(source);return 0; } };
static WorkLoop workloop;
class Clients { public: unsigned count=0;unsigned getCount() { return count; } };
class IOHIDEventService : public IOService {
public:
    WorkLoop *_hidWorkLoop=&workloop;
    void *_clientLock=nullptr;
    Clients clients;
    Clients *_clients=&clients;
    bool start(IOService *provider) { return provider!=nullptr; }
    void setProperty(const char *,const void *) {}
    void setProperty(const char *,unsigned long long,unsigned) {}
    void registerService() {}
};
static const int true_value=1;
static const void *kOSBooleanTrue=&true_value;
static const int kIOReturnSuccess=0,kHIDUsage_Csmr_Menu=0x40;
static void *IOLockAlloc() { return (void *)(uintptr_t)1; }
static void IOLockLock(void *p) { (void)p;++locks; }
static void IOLockUnlock(void *p) { (void)p;assert(locks);--locks; }
static int IOLog(const char *format,...) { assert(format);++logs;return 0; }
#define OSSafeReleaseNULL(p) do { (p)=nullptr; } while(0)
struct PE_Video { unsigned v_width,v_height; };
static unsigned dali=1,swipe_delay=60,wait_limit=600,width=1280,height=2772;
static bool swipe_found=true,wait_found=false,gui_ready=false;
static int PE_parse_boot_argn(const char *key,void *out,unsigned size) {
    assert(size==sizeof(unsigned));unsigned value=0;
    if(!strcmp(key,"sweetfb_native"))value=0;
    else if(!strcmp(key,"daligui"))value=dali;
    else if(!strcmp(key,"sweethidswipe")) { if(!swipe_found)return 0;value=swipe_delay; }
    else { assert(!strcmp(key,"sweethidwait"));if(!wait_found)return 0;value=wait_limit; }
    memcpy(out,&value,size);return 1;
}
static void PE_current_console(PE_Video *video) { video->v_width=width;video->v_height=height; }
static int sweet_dali_gui_presented() { return gui_ready; }
class Buttons {
public:
    unsigned presses=0,releases=0;
    void key(unsigned usage,bool down) {
        assert(!locks && usage==kHIDUsage_Csmr_Menu);
        if(down)++presses;else ++releases;
    }
};
static Buttons buttons;
static Buttons *gSweetButtons=&buttons;
class SweetHIDTouch : public IOHIDEventService {
public:
    void *fLock=nullptr;
    int fPhysicalW=0,fPhysicalH=0,fLogicalW=0,fLogicalH=0;
    bool fDaliCoordinates=false,fSwipeArmed=false,fSwipeActive=false;
    IOTimerEventSource *fSwipe=nullptr;
    UInt32 fSwipeDelay=0,fSwipeTicks=0,fSwipeReady=0,fSwipeStep=0,fSwipeArms=0,fSwipeLimit=0;
    bool fDown[10]={false};
    unsigned downs=0,ups=0;
    bool start(IOService *provider);
    static void swipeFired(OSObject *owner,IOTimerEventSource *sender);
    void touch(int finger,bool down,int x,int y) {
        assert(!locks && finger==0 && x>=0 && x<fPhysicalW && y>=0 && y<fPhysicalH);
        fDown[0]=down;if(down)++downs;else ++ups;
    }
};
static SweetHIDTouch *gSweetTouch;
'''


class LateGUIBudgetTest(unittest.TestCase):
    def run_code(self, body, cpp=False):
        compiler = (shutil.which('clang++') or shutil.which('c++')) if cpp else (shutil.which('clang') or shutil.which('cc'))
        self.assertIsNotNone(compiler)
        source = '#include <assert.h>\n#include <stdbool.h>\n#include <stdint.h>\n#include <stdio.h>\n#include <string.h>\n' + body
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / ('test.cpp' if cpp else 'test.c')
            path.write_text(source)
            subprocess.run([compiler, '-std=c++17' if cpp else '-std=c11', '-O2',
                            '-Wall', '-Wextra', '-Werror', str(path), '-o', str(root / 'test')], check=True)
            result = subprocess.run([str(root / 'test')], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'PASS')

    def test_defaults_bounds_clock_arithmetic_and_finite_expiry(self):
        self.run_code(WDT + WAIT + '''
int main(void) {
    assert(DALI_WDT_DEFAULT_SECONDS==120 && DALI_WDT_MAX_SECONDS==1200);
    assert(SWEET_HID_WAIT_DEFAULT_SECONDS==600 && SWEET_HID_WAIT_MAX_SECONDS==1200);
    for(unsigned n=0;n<1400;++n) {
        assert(dali_wdt_select_lease(n)==((n>=120 && n<=1200)?n:120));
        assert(sweet_hid_select_wait(n)==((n>=600 && n<=1200)?n:600));
    }
    assert(dali_wdt_select_lease(UINT32_MAX)==120 && sweet_hid_select_wait(UINT32_MAX)==600);
    uint64_t hz=13000000,start=1000*hz;
    for(unsigned n=120;n<=1200;++n) {
        assert(dali_wdt_reload_due(start,0,start+n*hz-1,hz,n));
        assert(!dali_wdt_reload_due(start,0,start+n*hz,hz,n));
    }
    assert(!dali_wdt_reload_due(start,0,start,0,1200));
    assert(!dali_wdt_reload_due(start,0,start,UINT64_MAX,1200));
    assert(!dali_wdt_reload_due(start,0,start-1,hz,1200));
    assert(!dali_wdt_reload_due(start,start+hz,start,hz,1200));
    assert(!dali_wdt_reload_due(start,start,start+hz-1,hz,1200));
    assert(dali_wdt_reload_due(start,start,start+hz,hz,1200));
    assert(!dali_wdt_reload_due(start,0,start,hz,1201));
    puts("PASS");return 0;
}
''')

    def test_actual_watchdog_poll_still_only_reloads_enabled_boot_cpu(self):
        source = fragment('pexpert/arm/pe_dali_watchdog.c',
                          ROOT / 'xnu/patches/0167-dali-configurable-finite-boot-watchdog-lease.patch')
        poll = source[source.index('void\ndali_watchdog_poll(void)'):source.index('\nvoid\ndali_watchdog_init(void)')]
        for old, new in {
            '__asm__ volatile("mrs %0, mpidr_el1" : "=r"(cpu));': 'cpu=test_cpu;',
            '__asm__ volatile("mrs %0, cntpct_el0" : "=r"(now));': 'now=test_now;',
            '__asm__ volatile("dsb sy" ::: "memory");': '++barriers;',
        }.items():
            self.assertEqual(poll.count(old), 1)
            poll = poll.replace(old, new)
        self.run_code(WDT + '''
#define DALI_WDT_RELOAD 0x1971u
static volatile uint32_t *dali_wdt;
static uint64_t dali_wdt_hz,dali_wdt_started,dali_wdt_last,test_cpu,test_now;
static unsigned dali_wdt_boot_seconds=120,barriers;
''' + poll + '''
int main(void) {
    volatile uint32_t regs[6]={0x17d,0xf000,0xfeed,0x11223344,0x55667788,0xaabbccdd};
    dali_watchdog_poll();assert(!barriers);
    dali_wdt=regs;dali_watchdog_poll();assert(!barriers);
    dali_wdt_hz=13000000;dali_wdt_started=1000*dali_wdt_hz;test_now=dali_wdt_started;
    test_cpu=1;dali_watchdog_poll();assert(!barriers);
    test_cpu=0;regs[0]=0x17c;dali_watchdog_poll();assert(!barriers && regs[2]==0xfeed);
    regs[0]=0x17d;dali_watchdog_poll();assert(barriers==1 && regs[2]==0x1971);
    test_now=dali_wdt_started+120*dali_wdt_hz;regs[2]=0xfeed;
    dali_watchdog_poll();assert(barriers==1 && regs[2]==0xfeed);
    dali_wdt_boot_seconds=dali_wdt_select_lease(1200);test_now=dali_wdt_started+720*dali_wdt_hz;
    dali_watchdog_poll();assert(barriers==2 && regs[2]==0x1971);
    regs[2]=0xfeed;dali_watchdog_poll();assert(barriers==2 && regs[2]==0xfeed);
    test_now=dali_wdt_started+1199*dali_wdt_hz;dali_watchdog_poll();assert(barriers==3);
    test_now=dali_wdt_started+1200*dali_wdt_hz;regs[2]=0xfeed;
    dali_watchdog_poll();assert(barriers==3 && regs[2]==0xfeed);
    assert(regs[0]==0x17d && regs[1]==0xf000 && regs[3]==0x11223344 && regs[4]==0x55667788 && regs[5]==0xaabbccdd);
    puts("PASS");return 0;
}
''')

    def test_actual_hid_startup_wait_alone_never_arms_and_defaults_preserved(self):
        self.run_code(WAIT + HID_FIXTURE + START + TIMER + '''
int main() {
    IOService provider;
    swipe_found=false;wait_found=true;wait_limit=1200;unsigned before=creations;
    SweetHIDTouch noinput;assert(noinput.start(&provider));assert(!noinput.fSwipe && creations==before);
    swipe_found=true;swipe_delay=0;SweetHIDTouch zero;assert(zero.start(&provider) && !zero.fSwipe);
    swipe_delay=301;SweetHIDTouch invalid;assert(invalid.start(&provider) && !invalid.fSwipe);
    swipe_delay=60;wait_found=false;SweetHIDTouch normal;
    assert(normal.start(&provider) && normal.fSwipe && normal.fSwipeLimit==600);
    wait_found=true;
    for(unsigned n=0;n<1400;++n) {
        wait_limit=n;SweetHIDTouch selected;assert(selected.start(&provider));
        assert(selected.fSwipe && selected.fSwipeLimit==sweet_hid_select_wait(n));
    }
    dali=0;SweetHIDTouch sweet;before=creations;
    assert(sweet.start(&provider) && !sweet.fSwipe && creations==before);
    assert(gSweetTouch==&sweet && logs>0 && !locks && !buttons.presses && !buttons.releases);
    puts("PASS");return 0;
}
''', cpp=True)

    def test_actual_timer_accepts_late_real_frame_with_delay_busy_guard_and_one_gesture(self):
        self.run_code(WAIT + HID_FIXTURE + START + TIMER + '''
static void fire(SweetHIDTouch &touch) { SweetHIDTouch::swipeFired(&touch,&timer); }
int main() {
    IOService provider;
    SweetHIDTouch normal;assert(normal.start(&provider));
    for(unsigned i=0;i<599;++i)fire(normal);
    unsigned scheduled=timer.calls;fire(normal);
    assert(normal.fSwipeTicks==600 && !normal.fSwipeActive && timer.calls==scheduled && !buttons.presses);
    wait_found=true;wait_limit=1200;SweetHIDTouch late;assert(late.start(&provider));
    late.clients.count=1;
    for(unsigned i=0;i<719;++i)fire(late);
    assert(late.fSwipeTicks==719 && !late.fSwipeArmed && !buttons.presses);
    gui_ready=true;fire(late);
    assert(late.fSwipeArmed && late.fSwipeReady==780 && !late.fSwipeActive);
    late.fDown[0]=true;
    while(late.fSwipeTicks<780)fire(late);
    assert(!late.fSwipeActive && !buttons.presses);
    late.fDown[0]=false;fire(late);
    assert(late.fSwipeActive && late.fSwipeStep==1 && buttons.presses==1);
    while(late.fSwipeStep<24)fire(late);
    assert(buttons.presses==1 && buttons.releases==1 && late.downs==21 && late.ups==1);
    scheduled=timer.calls;for(unsigned i=0;i<100;++i)fire(late);
    assert(timer.calls==scheduled && late.fSwipeStep==24 && buttons.presses==1);
    gui_ready=false;SweetHIDTouch expires;assert(expires.start(&provider));
    for(unsigned i=0;i<1199;++i)fire(expires);
    scheduled=timer.calls;fire(expires);
    assert(expires.fSwipeTicks==1200 && !expires.fSwipeActive && timer.calls==scheduled);
    assert(!locks && buttons.presses==1 && sweet_hid_select_wait(0)==600);
    assert(creations==3 && logs>0 && gSweetTouch==&expires);
    puts("PASS");return 0;
}
''', cpp=True)

    def test_scope_has_no_new_trace_or_watchdog_register_writes(self):
        text = PATCH.read_text()
        self.assertEqual(re.findall(r'^diff --git a/(\S+) b/', text, re.M),
                         ['pexpert/arm/DaliWatchdogPolicy.h', 'iokit/Kernel/arm/hid/SweetHID.cpp',
                          'iokit/Kernel/arm/hid/SweetHIDWaitPolicy.h'])
        self.assertIn('ready = ready && sweet_dali_gui_presented();', TIMER)
        self.assertIn('self->fSwipeTicks < self->fSwipeReady', TIMER)
        self.assertIn('bool busy = self->fDown[0];', TIMER)
        self.assertIn('self->fSwipeStep >= 24', TIMER)
        self.assertIn('self->fSwipeStep = 24;', TIMER)
        self.assertIn('Deliberately do not re-arm.', TIMER)
        self.assertIn('"sweethidswipe"', START)
        self.assertIn('fSwipeDelay >= 1 && fSwipeDelay <= 300', START)
        self.assertIn('"sweethidwait"', START)
        self.assertNotIn('PE_parse_boot_argn', TIMER)
        self.assertNotIn('dali_wdt[', text)
        self.assertNotIn('sweet_ff_', text)


if __name__ == '__main__':
    unittest.main()
