#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define SWEET_ULOCK_CAS(p, old, val) __extension__ ({ \
    __typeof__(*(p)) want_ = (old); \
    __atomic_compare_exchange_n((p), &want_, (val), 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE); \
})
#define SWEET_ULOCK_LOAD(p) __atomic_load_n((p), __ATOMIC_ACQUIRE)
#define SWEET_ULOCK_STORE(p, v) __atomic_store_n((p), (v), __ATOMIC_RELEASE)
#define SWEET_ULOCK_INC(p) __atomic_add_fetch((p), 1, __ATOMIC_RELAXED)
#include "SweetUlockTrace.h"

static struct sweet_ulock_record event(uint64_t tid, uint64_t owner)
{
    struct sweet_ulock_record r = {
        .tid=tid, .owner_tid=owner, .address=tid * 16, .value=0x1234,
        .timeout_ns=0, .begin=1000+tid, .pc=0x1111, .lr=0x2222,
        .opcode=owner ? 2 : 1, .flags=0x10000
    };
    return r;
}

static void basics(void)
{
    struct sweet_ulock_trace t = {0};
    struct sweet_ulock_snapshot s = {0};
    struct sweet_ulock_record a = event(221, 345), b = event(222, 0);
    uint64_t token_a = sweet_ulock_record_begin(&t, &a);
    uint64_t token_b = sweet_ulock_record_begin(&t, &b);
    assert(token_a && token_b && token_a != token_b);
    assert(sweet_ulock_record_snapshot(&t, &s) == 0 && s.count == 2);
    assert(s.records[0].tid == 221 && s.records[0].owner_tid == 345);
    assert(s.records[1].tid == 222 && s.records[1].owner_tid == 0);
    assert(s.records[1].opcode == 1 && s.records[1].address == 222 * 16);
    assert(s.records[0].pc == 0x1111 && s.records[0].lr == 0x2222);

    /* Finish does not depend on the producer/sampler guard. */
    SWEET_ULOCK_STORE(&t.guard, 1u);
    sweet_ulock_record_end(&t, token_b);
    assert(SWEET_ULOCK_LOAD(&t.slots[token_b & 15].token) == 0);
    assert(sweet_ulock_record_begin(&t, &b) == 0);
    assert(sweet_ulock_record_snapshot(&t, &s) == -1);
    SWEET_ULOCK_STORE(&t.guard, 0u);
    assert(sweet_ulock_record_snapshot(&t, &s) == 0 && s.count == 1 && s.busy_drop == 1);

    /* A stale finish must not erase the next occupant of the same slot. */
    uint64_t token_b2 = sweet_ulock_record_begin(&t, &b);
    assert(token_b2 && token_b2 != token_b && (token_b2 & 15) == (token_b & 15));
    sweet_ulock_record_end(&t, token_b);
    assert(sweet_ulock_record_snapshot(&t, &s) == 0 && s.count == 2);
    assert(s.records[1].token == token_b2);
    sweet_ulock_record_end(&t, token_b2);

    /* A long-lived wait survives arbitrary short waits; slots are not a lifetime ring. */
    for (unsigned int i = 0; i < 10000; ++i) {
        uint64_t x = sweet_ulock_record_begin(&t, &b);
        assert(x);
        sweet_ulock_record_end(&t, x);
    }
    assert(sweet_ulock_record_snapshot(&t, &s) == 0 && s.count == 1 && s.records[0].token == token_a);
    sweet_ulock_record_close(&t);
    assert(sweet_ulock_record_begin(&t, &b) == 0);
    sweet_ulock_record_end(&t, token_a);
    assert(sweet_ulock_record_snapshot(&t, &s) == 0 && s.count == 0);
}

static void bounds(void)
{
    struct sweet_ulock_trace t = {0};
    struct sweet_ulock_snapshot s = {0};
    struct sweet_ulock_record r = event(1, 0);
    uint64_t tokens[SWEET_ULOCK_SLOTS];
    for (unsigned int i=0; i<SWEET_ULOCK_SLOTS; ++i) {
        r.tid = i+1;
        tokens[i] = sweet_ulock_record_begin(&t, &r);
        assert(tokens[i]);
    }
    assert(!sweet_ulock_record_begin(&t, &r));
    assert(sweet_ulock_record_snapshot(&t, &s) == 0);
    assert(s.count == SWEET_ULOCK_SLOTS && s.full_drop == 1);
    for (unsigned int i=0; i<SWEET_ULOCK_SLOTS; ++i) sweet_ulock_record_end(&t, tokens[i]);
    t.generation = UINT64_MAX >> SWEET_ULOCK_INDEX_BITS;
    assert(!sweet_ulock_record_begin(&t, &r));
    assert(sweet_ulock_record_snapshot(&t, &s) == 0 && s.exhausted == 1);
}

static struct sweet_ulock_trace concurrent;
static unsigned int running;
static void *worker(void *arg)
{
    uint64_t tid = (uintptr_t)arg;
    struct sweet_ulock_record r = event(tid, tid + 100);
    for (unsigned int i=0; i<25000; ++i) {
        uint64_t token = sweet_ulock_record_begin(&concurrent, &r);
        if (token) sweet_ulock_record_end(&concurrent, token);
    }
    __atomic_sub_fetch(&running, 1u, __ATOMIC_RELEASE);
    return NULL;
}
static void concurrency(void)
{
    pthread_t threads[4];
    running = 4;
    for (unsigned int i=0; i<4; ++i)
        assert(pthread_create(&threads[i], NULL, worker, (void *)(uintptr_t)(i + 1001)) == 0);
    while (__atomic_load_n(&running, __ATOMIC_ACQUIRE)) {
        struct sweet_ulock_snapshot s;
        if (sweet_ulock_record_snapshot(&concurrent, &s)) continue;
        for (unsigned int j=0; j<s.count; ++j) {
            const struct sweet_ulock_record *r = &s.records[j];
            assert(r->token && r->tid >= 1001 && r->tid <= 1004);
            assert(r->owner_tid == r->tid + 100 && r->address == r->tid * 16);
        }
    }
    for (unsigned int i=0; i<4; ++i) assert(pthread_join(threads[i], NULL) == 0);
    struct sweet_ulock_snapshot s;
    assert(sweet_ulock_record_snapshot(&concurrent, &s) == 0 && s.count == 0);
}

int main(void)
{
    basics(); bounds(); concurrency();
    puts("ulock trace fixture: PASS");
    return 0;
}
