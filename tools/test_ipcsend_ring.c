#include <assert.h>
#include <stdbool.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static bool
host_cas(uint32_t expected, uint32_t desired, volatile uint32_t *ptr)
{
    return __atomic_compare_exchange_n((uint32_t *)ptr, &expected, desired,
        false, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED);
}
#define SWEET_IPCSEND_CAS(e, d, p) host_cas((e), (d), (p))
#define SWEET_IPCSEND_INC(p) __atomic_add_fetch((p), 1u, __ATOMIC_RELAXED)
#define SWEET_IPCSEND_LOAD(p) __atomic_load_n((p), __ATOMIC_RELAXED)
#define SWEET_IPCSEND_CLOSED(p) __atomic_load_n((p), __ATOMIC_ACQUIRE)
#define SWEET_IPCSEND_CLOSE(p) __atomic_store_n((p), 1u, __ATOMIC_RELEASE)
#include "SweetIPCSendTrace.h"

static struct sweet_ipcsend_ring ring;
static struct sweet_ipcsend_ring lifetime_ring;
static struct sweet_ipcsend_ring concurrent_ring;

static struct sweet_ipcsend_record
make_record(unsigned phase, uint64_t token)
{
    return (struct sweet_ipcsend_record){
        .token = token, .tid = 0x136, .target = 1, .phase = phase,
        .msgcount = 64, .qlimit = 64, .fullwaiters = 1,
        .receiver_pid = 26, .wait_result = (int32_t)phase,
    };
}

static void *
producer(void *unused)
{
    (void)unused;
    for (unsigned i = 0; i < 2000; ++i) {
        struct sweet_ipcsend_record r = make_record(SWEET_IPCSEND_ENTRY, i);
        (void)sweet_ipcsend_ring_append(&concurrent_ring, &r);
    }
    return NULL;
}

int
main(void)
{
    struct sweet_ipcsend_snapshot out;
    struct sweet_ipcsend_record r;

    /* Disabled targets do not enter the ring. */
    r = make_record(SWEET_IPCSEND_ENTRY, 1);
    r.target = 0;
    assert(sweet_ipcsend_ring_append(&ring, &r) == 1);
    assert(sweet_ipcsend_ring_drain(&ring, &out) == 0);
    assert(out.count == 0 && out.lost == 0);

    /* Matching wait entry/exit survive a sample drain. */
    r = make_record(SWEET_IPCSEND_ENTRY, 0xabc);
    assert(sweet_ipcsend_ring_append(&ring, &r) == 0);
    r = make_record(SWEET_IPCSEND_EXIT, 0xabc);
    r.wait_result = 2;
    assert(sweet_ipcsend_ring_append(&ring, &r) == 0);
    assert(sweet_ipcsend_ring_drain(&ring, &out) == 0);
    assert(out.count == 2 && out.used == 2 && out.lost == 0);
    assert(out.records[0].token == out.records[1].token);
    assert(out.records[0].phase == SWEET_IPCSEND_ENTRY);
    assert(out.records[1].phase == SWEET_IPCSEND_EXIT);
    assert(out.records[1].wait_result == 2 && out.records[1].receiver_pid == 26);

    /* Periodic drains never reset the 128-record lifetime cap. */
    unsigned captured = 0;
    for (unsigned i = 0; i < SWEET_IPCSEND_CAPACITY + 1; ++i) {
        r = make_record(SWEET_IPCSEND_ENTRY, i);
        (void)sweet_ipcsend_ring_append(&lifetime_ring, &r);
        if ((i % 16) == 15) {
            assert(sweet_ipcsend_ring_drain(&lifetime_ring, &out) == 0);
            captured += out.count;
        }
    }
    assert(sweet_ipcsend_ring_drain(&lifetime_ring, &out) == 0);
    captured += out.count;
    assert(captured == SWEET_IPCSEND_CAPACITY);
    assert(out.count == 0 && out.used == SWEET_IPCSEND_CAPACITY && out.lost == 1);
    r = make_record(SWEET_IPCSEND_ENTRY, SWEET_IPCSEND_CAPACITY + 1);
    assert(sweet_ipcsend_ring_append(&lifetime_ring, &r) == -1);
    assert(sweet_ipcsend_ring_drain(&lifetime_ring, &out) == 0 && out.lost == 2);

    /* Producer guard is try-only; collision is recorded rather than waited out. */
    __atomic_store_n((uint32_t *)&ring.guard, 1u, __ATOMIC_RELEASE);
    r = make_record(SWEET_IPCSEND_EXIT, 99);
    assert(sweet_ipcsend_ring_append(&ring, &r) == -1);
    __atomic_store_n((uint32_t *)&ring.guard, 0u, __ATOMIC_RELEASE);
    assert(sweet_ipcsend_ring_drain(&ring, &out) == 0);
    assert(out.count == 0 && out.lost == 1);

    /* Concurrent producers remain bounded and account for dropped records. */
    pthread_t threads[8];
    for (unsigned i = 0; i < 8; ++i) assert(pthread_create(&threads[i], NULL, producer, NULL) == 0);
    for (unsigned i = 0; i < 8; ++i) assert(pthread_join(threads[i], NULL) == 0);
    assert(sweet_ipcsend_ring_drain(&concurrent_ring, &out) == 0);
    assert(out.count <= SWEET_IPCSEND_CAPACITY && out.used == out.count && out.lost > 0);

    /* Final sample closes production, preserves pending records, rejects later sends. */
    r = make_record(SWEET_IPCSEND_ENTRY, 0xdef);
    assert(sweet_ipcsend_ring_append(&ring, &r) == 0);
    sweet_ipcsend_ring_close(&ring);
    r = make_record(SWEET_IPCSEND_EXIT, 0xfed);
    assert(sweet_ipcsend_ring_append(&ring, &r) == 1);
    assert(sweet_ipcsend_ring_drain(&ring, &out) == 0);
    assert(out.count == 1 && out.records[0].token == 0xdef);
    assert(__atomic_load_n((uint32_t *)&ring.closed, __ATOMIC_ACQUIRE) == 1);

    puts("PASS: ring disabled, ENTRY/EXIT, periodic lifetime cap, contention, concurrency, final close");
    return 0;
}
