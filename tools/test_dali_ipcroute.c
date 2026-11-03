#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define SWEET_IPCSEND_CAS(old, val, p) __extension__ ({ uint32_t expected_=(old); __atomic_compare_exchange_n((p),&expected_,(val),0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE); })
#define SWEET_IPCSEND_INC(p) __atomic_add_fetch((p),1,__ATOMIC_RELAXED)
#define SWEET_IPCSEND_LOAD(p) __atomic_load_n((p),__ATOMIC_RELAXED)
#define SWEET_IPCSEND_CLOSED(p) __atomic_load_n((p),__ATOMIC_ACQUIRE)
#define SWEET_IPCSEND_CLOSE(p) __atomic_store_n((p),1u,__ATOMIC_RELEASE)
#include <SweetIPCSendTrace.h>

static struct sweet_ipcroute_record event(uint64_t tid, uint64_t round)
{
    return (struct sweet_ipcroute_record){.tid=tid,.at=round,.pc=0x1000+tid,
        .lr=0x2000+tid,.receiver_pid=(int32_t)tid,.msg_id=(int32_t)round,
        .receiver_port=(uint32_t)tid,.option=7,.count=2,.limit=6,.user_context_valid=1};
}
static void unit(void)
{
    struct sweet_ipcroute_book b={0}; struct sweet_ipcroute_snapshot s={0};
    struct sweet_ipcroute_record r=event(42,1);
    sweet_ipcroute_record(&b,&r);
    assert(!sweet_ipcroute_snapshot(&b,&s) && s.count==1 && s.next_seq==1);
    assert(s.records[0].tid==42 && s.records[0].at==1 && s.records[0].seq==1);
    r.at=2; sweet_ipcroute_record(&b,&r);
    assert(!sweet_ipcroute_snapshot(&b,&s) && s.count==1 && s.next_seq==2 && s.records[0].at==2);
    for(unsigned i=0;i<15;i++){r=event(100+i,i);sweet_ipcroute_record(&b,&r);}
    assert(!sweet_ipcroute_snapshot(&b,&s) && s.count==16 && s.evictions==0);
    r=event(300,22);sweet_ipcroute_record(&b,&r);
    assert(!sweet_ipcroute_snapshot(&b,&s) && s.count==16 && s.evictions==1);
    assert(s.records[0].tid==300 && s.records[0].seq==18); /* oldest tid=42 */
    r=event(100,44);sweet_ipcroute_record(&b,&r);
    assert(!sweet_ipcroute_snapshot(&b,&s) && s.evictions==1 && s.next_seq==19);
    SWEET_IPCSEND_CAS(0,1,&b.guard);
    sweet_ipcroute_record(&b,&r);
    assert(sweet_ipcroute_snapshot(&b,&s)==-1);
    SWEET_IPCSEND_CAS(1,0,&b.guard);
    assert(!sweet_ipcroute_snapshot(&b,&s) && s.dropped==1 && s.next_seq==19);
    sweet_ipcroute_close(&b); sweet_ipcroute_record(&b,&r);
    assert(!sweet_ipcroute_snapshot(&b,&s) && s.next_seq==19);
    struct sweet_ipcroute_book overflow={0};overflow.next_seq=UINT64_MAX;
    sweet_ipcroute_record(&overflow,&r);
    assert(!sweet_ipcroute_snapshot(&overflow,&s) && s.exhausted==1 && s.count==0);
}
static struct sweet_ipcroute_book concurrent;
static unsigned running;
static void *producer(void *arg)
{
    uint64_t tid=(uintptr_t)arg;
    for(uint64_t i=1;i<=30000;i++){struct sweet_ipcroute_record r=event(tid,i);sweet_ipcroute_record(&concurrent,&r);}
    __atomic_sub_fetch(&running,1,__ATOMIC_RELEASE);return NULL;
}
static void concurrency(void)
{
    pthread_t th[4];running=4;
    for(unsigned i=0;i<4;i++)assert(!pthread_create(&th[i],0,producer,(void *)(uintptr_t)(i+1001)));
    uint64_t previous=0;unsigned snapshots=0;
    while(__atomic_load_n(&running,__ATOMIC_ACQUIRE)){
        struct sweet_ipcroute_snapshot s;if(sweet_ipcroute_snapshot(&concurrent,&s))continue;
        assert(s.count<=4 && s.next_seq>=previous);previous=s.next_seq;
        for(unsigned i=0;i<s.count;i++){
            const struct sweet_ipcroute_record *r=&s.records[i];
            assert(r->seq>0 && r->seq<=s.next_seq && r->tid>=1001 && r->tid<=1004);
            assert(r->pc==0x1000+r->tid && r->lr==0x2000+r->tid && r->receiver_pid==(int)r->tid);
        }
        snapshots++;
    }
    for(unsigned i=0;i<4;i++)assert(!pthread_join(th[i],0));
    assert(snapshots>0);
}
int main(void){unit();concurrency();puts("IPC route policy fixture: PASS");return 0;}
