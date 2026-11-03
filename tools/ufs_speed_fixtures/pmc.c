#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#ifdef NEGATIVE_CONTROL
#include "original/SweetUFSPWMPolicy.h"
#else
#include "SweetUFSPWMPolicy.h"
#endif
#define REG_IS 0x20u
#define REG_IE 0x24u
#define REG_HCS 0x30u
#define REG_HCE 0x34u
#define REG_UTRLBA 0x50u
#define REG_UTRLBAU 0x54u
#define REG_UTRLDBR 0x58u
#define REG_UTRLRSR 0x60u
#define REG_UTMRLDBR 0x78u
#define REG_UTMRLRSR 0x80u
#define REG_UICCMD 0x90u
#define REG_UICCMDARG1 0x94u
#define REG_UICCMDARG2 0x98u
#define REG_UICCMDARG3 0x9cu
#define REG_AHIT 0x18u
#define DMA_UTRL 0x1000u
#define NSEC_PER_MSEC 1u
#define NSEC_PER_SEC 1000000ull
#define os_atomic_cmpxchg(p,o,n,ord) __sync_bool_compare_and_swap((p),(o),(n))
#define os_atomic_load(p,ord) __atomic_load_n((p),__ATOMIC_SEQ_CST)
struct { bool dali, ready, command_poisoned; uint64_t dma_pa; } su;
static uint32_t sweet_ufs_pwm_terminal;
struct sample { uint32_t status, hcs, arg2; };
static struct sample samples[8];
static unsigned nsamples, nstatus, current, arg2reads, commands, acks, writes, times;
static uint32_t regs[256], final_hcs, post_hcs, seen;
static bool submitted, acked, awaiting_poll_hcs, frozen, final_bad_hce, final_bad_doorbell;
static uint64_t tick;
static uint64_t mach_absolute_time(void) { ++times; return frozen ? tick : tick++; }
static void nanoseconds_to_absolutetime(uint64_t n, uint64_t *out) { *out = n; }
static void delay(uint32_t n) { if (!frozen) tick += n; }
static uint32_t ufs_rd(uint32_t reg) {
    if (reg == REG_IS && submitted && !acked) {
        current = nstatus < nsamples ? nstatus : nsamples - 1;
        nstatus++;
        awaiting_poll_hcs = true;
        seen |= samples[current].status;
        return samples[current].status;
    }
    if (reg == REG_HCS && submitted) {
        if (awaiting_poll_hcs) { awaiting_poll_hcs=false; return samples[current].hcs; }
        return acked ? post_hcs : final_hcs;
    }
    if (reg == REG_UICCMDARG2 && submitted && !acked) {
        arg2reads++; return samples[current].arg2;
    }
    if (reg == REG_HCE && submitted && final_bad_hce) return 0;
    if (reg == REG_UTRLDBR && submitted && final_bad_doorbell) return 1;
    return regs[reg];
}
static void ufs_wr(uint32_t reg, uint32_t value) {
    writes++;
    switch (reg) {
    case REG_UICCMDARG1: assert(!submitted && value == 0x15710000u); break;
    case REG_UICCMDARG2: assert(!submitted && value == 0); break;
    case REG_UICCMDARG3: assert(!submitted && value == 0x44); break;
    case REG_UICCMD: assert(!submitted && value == 2); submitted=true; commands++; break;
    case REG_IS:
        assert(submitted && !acked && value == (SWEET_PWM_UCCS|SWEET_PWM_UPMS));
        assert((seen & (SWEET_PWM_UCCS|SWEET_PWM_UPMS)) == (SWEET_PWM_UCCS|SWEET_PWM_UPMS));
        assert(arg2reads == 1);
        acked=true; acks++; regs[REG_IS]=0; break;
    default: assert(!"unexpected MMIO write (including no SCSI doorbell writes)");
    }
}
/* INSERT_PRODUCTION_FUNCTIONS */
static unsigned snapshots, sets, platform_adopts, platform_validates;
static uint32_t readback_mode, readback_term, readback_series;
static int ufs_dali_hs_platform_adopt(void) { platform_adopts++; return 0; }
static int ufs_dali_hs_platform_validate(void) { platform_validates++; return 0; }
static int ufs_pwm_snapshot(struct sweet_pwm_snapshot *s, uint64_t until) {
    (void)until;
    if (snapshots++ == 0) {
        *s=(struct sweet_pwm_snapshot){0x55,1,1,1,1,2,2,0,0};
    } else {
        assert(acked && commands == 1);
        *s=(struct sweet_pwm_snapshot){readback_mode,1,1,1,1,2,2,readback_term,readback_term};
    }
    return 0;
}
static int ufs_pwm_get(bool peer, uint32_t attribute, uint32_t *value, uint64_t until) {
    (void)peer; (void)until;
    int err=ufs_pwm_reentry_error(); if (err) return err;
    if (attribute==0x1587) { *value=5; return 0; }
    assert(attribute==0x156a && acked); *value=readback_series; return 0;
}
static int ufs_pwm_set(uint32_t attribute, uint32_t value, uint64_t until) {
    (void)attribute; (void)value; (void)until;
    int err=ufs_pwm_reentry_error(); if (err) return err;
    assert(!submitted); sets++; return 0;
}
#include "hsg1-snapshot.h"
static void reset(void) {
    memset(&su,0,sizeof(su)); memset(regs,0,sizeof(regs)); memset(samples,0,sizeof(samples));
    sweet_ufs_pwm_terminal=0; su.dali=true; su.dma_pa=0x123456780000ull;
    regs[REG_HCE]=1; regs[REG_HCS]=0x0b; regs[REG_UTRLRSR]=1;
    regs[REG_UTRLBA]=(uint32_t)(su.dma_pa+DMA_UTRL);
    regs[REG_UTRLBAU]=(uint32_t)((su.dma_pa+DMA_UTRL)>>32);
    final_hcs=post_hcs=0x10b; nsamples=1; nstatus=current=arg2reads=commands=acks=writes=times=seen=0;
    submitted=acked=awaiting_poll_hcs=frozen=final_bad_hce=final_bad_doorbell=false; tick=1;
    snapshots=sets=platform_adopts=platform_validates=0;
    readback_mode=0x44; readback_term=1; readback_series=2;
}
static int run(void) { return ufs_pwm_pmc(0x44,1000000u); }
static void event(unsigned i,uint32_t is,uint32_t hcs,uint32_t arg2) {
    assert(i<8); samples[i]=(struct sample){is,hcs,arg2}; if (nsamples<=i) nsamples=i+1;
}
static void expected(const char *name, int got, int want, unsigned cmd, unsigned ack) {
    if (got != want || commands != cmd || acks != ack) {
        fprintf(stderr,"FAIL %s got=%d want=%d cmd=%u/%u ack=%u/%u\n",name,got,want,commands,cmd,acks,ack);
        assert(0);
    }
    printf("PASS %s ret=%d commands=%u ack=%u polls=%u arg2reads=%u\n",name,got,commands,acks,nstatus,arg2reads);
}
static void transient_cases(void) {
    int ret;
    reset(); event(0,0x400,0x8,0); event(1,0x410,0x10b,0); ret=run();
#ifdef NEGATIVE_CONTROL
    expected("live-first-sample-then-UPMS",ret,EIO,1,0);
#else
    expected("live-first-sample-then-UPMS",ret,0,1,1);
#endif
    assert(arg2reads==1);
    reset(); event(0,0x10,0x108,0); event(1,0x410,0x10b,0); ret=run();
#ifdef NEGATIVE_CONTROL
    expected("UPMS-first-no-DP",ret,EIO,1,0);
#else
    expected("UPMS-first-no-DP",ret,0,1,1);
#endif
    reset(); event(0,0x410,0x108,0); ret=run();
#ifdef NEGATIVE_CONTROL
    expected("both-before-final-readiness",ret,EIO,1,0);
#else
    expected("both-before-final-readiness",ret,0,1,1);
#endif
}
int main(void) {
    int ret; transient_cases();
    reset(); event(0,0x400,0xb,0); event(1,0x410,0x10b,0); ret=run(); expected("UCCS-first",ret,0,1,1); assert(arg2reads==1);
    reset(); event(0,0x10,0x10b,0); event(1,0x410,0x10b,0); ret=run(); expected("UPMS-first",ret,0,1,1);
    reset(); event(0,0x400,0x1,0); event(1,0x410,0x10b,0); ret=run(); expected("pending-not-uic-ready",ret,0,1,1);
    reset(); event(0,0x410,0x10b,0); final_hcs=0x108; ret=run(); expected("no-final-DP",ret,EIO,1,0);
    reset(); event(0,0x410,0x10b,0); final_hcs=0x109; ret=run(); expected("no-final-transfer-ready",ret,EIO,1,0);
    reset(); event(0,0x410,0x10b,0); final_hcs=0x103; ret=run(); expected("no-final-UIC-ready",ret,EIO,1,0);
    reset(); event(0,0x410,0x10b,0); post_hcs=0x108; ret=run(); expected("no-post-ack-DP",ret,EIO,1,1);
    reset(); regs[REG_HCS]=8; ret=run(); expected("no-initial-DP",ret,EIO,0,0);
    reset(); regs[REG_HCS]=3; ret=run(); expected("no-initial-UIC-ready",ret,EIO,0,0);
    reset(); regs[REG_HCE]=0; ret=run(); expected("pre-HCE",ret,EIO,0,0);
    reset(); regs[REG_IS]=0x400; ret=run(); expected("pre-stale-UCCS",ret,EIO,0,0);
    reset(); regs[REG_UTRLDBR]=1; ret=run(); expected("pre-transfer-doorbell",ret,EIO,0,0);
    reset(); regs[REG_UTMRLDBR]=1; ret=run(); expected("pre-task-doorbell",ret,EIO,0,0);
    reset(); regs[REG_UTRLBA]^=1; ret=run(); expected("pre-owner-list",ret,EIO,0,0);
    reset(); event(0,0x410,0x10b,0); final_bad_hce=true; ret=run(); expected("final-HCE",ret,EIO,1,0);
    reset(); event(0,0x410,0x10b,0); final_bad_doorbell=true; ret=run(); expected("final-doorbell",ret,EIO,1,0);
    reset(); event(0,0x404,0xb,0); ret=run(); expected("UIC-error",ret,EIO,1,0);
    reset(); event(0,0x480,0xb,0); ret=run(); expected("link-lost",ret,EIO,1,0);
    reset(); event(0,0x10400,0xb,0); ret=run(); expected("fatal-interrupt",ret,EIO,1,0);
    reset(); event(0,0x400,0x1b,0); ret=run(); expected("host-error",ret,EIO,1,0);
    reset(); event(0,0x400,0x2b,0); ret=run(); expected("device-error",ret,EIO,1,0);
    reset(); event(0,UINT32_MAX,0xb,0); ret=run(); expected("IS-unreadable",ret,EIO,1,0); assert(!arg2reads);
    reset(); event(0,0x400,UINT32_MAX,0); ret=run(); expected("HCS-unreadable",ret,EIO,1,0);
    reset(); event(0,0x400,0xb,1); ret=run(); expected("ARG2-failure",ret,EIO,1,0);
    reset(); event(0,0x410,0x20b,0); ret=run(); expected("UPMCRS-remote",ret,EIO,1,0);
    reset(); event(0,0x410,0xb,0); ret=run(); expected("UPMCRS-zero",ret,EIO,1,0);
    reset(); event(0,0x400,0xb,0); frozen=true; ret=run(); expected("missing-UPMS-bounded",ret,ETIMEDOUT,1,0); assert(nstatus==50001);
    reset(); event(0,0x10,0x10b,0); frozen=true; ret=run(); expected("missing-UCCS-bounded",ret,ETIMEDOUT,1,0); assert(nstatus==50001 && !arg2reads);
    reset(); event(0,0x0,0xb,0); ret=run(); expected("deadline",ret,ETIMEDOUT,1,0);
    reset(); event(0,0x480,0xb,0); ret=run(); assert(ret==EIO);
    assert(ufs_pwm_failure(ret)==EIO); assert(ufs_pwm_failure(ETIMEDOUT)==EIO); assert(ufs_pwm_reentry_error()==EIO);
    ret=run(); expected("terminal-latch-no-second-command",ret,EIO,1,0); assert(writes==4);
    reset(); event(0,0x400,0x8,0); event(1,0x410,0x10b,0); ret=ufs_dali_prepare_hsg1(true);
#ifdef NEGATIVE_CONTROL
    expected("HS-caller-live-first-frame",ret,EIO,1,0); assert(snapshots==1 && (sweet_ufs_pwm_terminal & 1));
#else
    expected("HS-caller-live-first-frame",ret,0,1,1); assert(snapshots==2 && sweet_ufs_pwm_terminal==0);
#endif
    assert(sets==17);
    reset(); event(0,0x410,0x10b,0); readback_mode=0x55; ret=ufs_dali_prepare_hsg1(true);
    expected("HS-caller-mode-readback-rejected",ret,EIO,1,1); assert(snapshots==2 && sweet_ufs_pwm_terminal==2u*EIO);
    reset(); event(0,0x410,0x10b,0); readback_term=0; ret=ufs_dali_prepare_hsg1(true);
    expected("HS-caller-term-readback-rejected",ret,EIO,1,1); assert(snapshots==2 && sweet_ufs_pwm_terminal==2u*EIO);
    reset(); event(0,0x410,0x10b,0); readback_series=1; ret=ufs_dali_prepare_hsg1(true);
    expected("HS-caller-series-readback-rejected",ret,EIO,1,1); assert(snapshots==2 && sweet_ufs_pwm_terminal==2u*EIO);
    reset(); event(0,0x410,0x20b,0); ret=ufs_dali_prepare_hsg1(true);
    expected("HS-caller-PWR-remote-no-readback",ret,EIO,1,0); assert(snapshots==1 && (sweet_ufs_pwm_terminal & 1));
    reset(); event(0,0x400,0xb,0); frozen=true; ret=ufs_dali_prepare_hsg1(true);
    expected("HS-caller-timeout-no-readback",ret,ETIMEDOUT,1,0); assert(snapshots==1 && (sweet_ufs_pwm_terminal & 1));
    puts("PASS all actual PMC/idle/atomic latch and HS readback cases");
    return 0;
}
