#!/usr/bin/env python3
"""Replay 0208 exactly and host-test the actual HFS fsync function."""
from pathlib import Path
import gzip
import hashlib
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE_GZ = ROOT / 'tools/hfs_fsync_tests/hfs_vnops.before.c.gz'
PATCH = ROOT / 'xnu/patches/0208-sweet-hfs-nonjournal-full-fsync-error.patch'
EXPECTED_BEFORE = '7f40fd11dd6ba6ee976a51858cac11a2d10d2e991aaf070a05693704a1ccd219'
EXPECTED_AFTER = '1863c19fc3231151be9f800147d0a5ba72967ad98a8432284e1b13d4c460e4d9'
EXPECTED_PATCH = '898da76194126c9ecbec9e439928ff0af2d4ea367be303d391fb0f9adb88749a'
BASE_BYTES = gzip.decompress(BASE_GZ.read_bytes())
assert hashlib.sha256(BASE_BYTES).hexdigest() == EXPECTED_BEFORE
assert hashlib.sha256(PATCH.read_bytes()).hexdigest() == EXPECTED_PATCH

prefix = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

typedef enum { HFS_FSYNC, HFS_FSYNC_FULL, HFS_FSYNC_BARRIER } hfs_fsync_mode_t;
typedef int64_t daddr64_t;
struct proc { int unused; };
struct filefork { unsigned int ff_unallocblocks; int ff_invalidranges; void *fcbBTCBPtr; };
struct cnode {
    unsigned int c_flag, c_bsdflags;
    long c_zftimeout;
    int c_truncatelockowner, c_hint, c_touch_acctime, c_touch_chgtime, c_touch_modtime;
};
struct hfsmount { void *jnl; };
struct vnode { struct cnode *cp; struct hfsmount *mp; struct filefork *fp; int isdir, issystem; };
#define VTOC(v) ((v)->cp)
#define VTOHFS(v) ((v)->mp)
#define VTOF(v) ((v)->fp)
#define MNT_WAIT 1
#define MNT_DWAIT 2
#define HFS_EXCLUSIVE_LOCK 1
#define HFS_SHARED_LOCK 2
#define HFS_SHARED_OWNER 2
#define HFS_LOCK_DEFAULT 0
#define HFS_LOCK_ALLOW_NOEXISTS 1
#define HFS_FSYNC_MODE_UNUSED 0
#define HFS_FSYNC_NOOP 0
#define HFS_FSYNC_CACHE_TEST 0
#define HFS_FSYNC_FULL_TEST 1
#define HFS_FLUSH_CACHE 1
#define HFS_FLUSH_FULL 2
#define HFS_FLUSH_JOURNAL_BARRIER 3
#define HFS_UPDATE_FORCE 1
#define IO_SYNC 1
#define C_ALWAYS_ZEROFILL (1U << 0)
#define C_ZFWANTSYNC (1U << 1)
#define C_DELETED (1U << 2)
#define C_NOEXISTS (1U << 3)
#define UF_NODUMP (1U << 4)
#define FALSE 0
#define TAILQ_EMPTY(p) (*(p) == 0)
#define ISSET(v, bits) (((v) & (bits)) != 0)

static int always_do_fullfsync;
static int stub_metadata_error, stub_flush_error, stub_update_error;
static int metadata_calls, flush_calls, update_calls, last_flush_mode;
static char order[16];
static unsigned order_n;
static void event(char c) { assert(order_n + 1 < sizeof(order)); order[order_n++] = c; order[order_n] = '\0'; }
static int vnode_isdir(struct vnode *v) { return v->isdir; }
static int vnode_isreg(struct vnode *v) { return !v->isdir; }
static int vnode_issystem(struct vnode *v) { return v->issystem; }
static int vnode_isswap(struct vnode *v) { (void)v; return 0; }
static int vnode_islnk(struct vnode *v) { (void)v; return 0; }
static int vnode_isrecycled(struct vnode *v) { (void)v; return 0; }
static int vnode_hasdirtyblks(struct vnode *v) { (void)v; return 0; }
static void vnode_cleardirty(struct vnode *v) { (void)v; }
static void hfs_unlock(struct cnode *c) { (void)c; }
static int hfs_lock(struct cnode *c, int a, int b) { (void)c; (void)a; (void)b; return 0; }
static void hfs_lock_truncate(struct cnode *c, int a, int b) { (void)c; (void)a; (void)b; }
static void hfs_unlock_truncate(struct cnode *c, int a) { (void)c; (void)a; }
static void BTFlushPath(struct filefork *f) { (void)f; }
static void BTSetLastSync(struct filefork *f, long t) { (void)f; (void)t; }
static int cluster_push(struct vnode *v, int f) { (void)v; (void)f; return 0; }
static void hfs_flush_invalid_ranges(struct vnode *v) { (void)v; }
static void buf_flushdirtyblks(struct vnode *v, int w, int f, const char *s) { (void)v; (void)w; (void)f; (void)s; }
static void microuptime(struct timeval *t) { t->tv_sec = 100; t->tv_usec = 0; }
static int hfs_update(struct vnode *v, int options) { (void)v; assert(options == HFS_UPDATE_FORCE); ++update_calls; event('U'); return stub_update_error; }
static int hfs_metasync(struct hfsmount *h, long hint, struct proc *p) { (void)h; (void)hint; (void)p; return 0; }
static int hfs_metasync_all(struct hfsmount *h) { (void)h; ++metadata_calls; event('M'); return stub_metadata_error; }
static int hfs_flush(struct hfsmount *h, int mode) { (void)h; ++flush_calls; last_flush_mode = mode; event('F'); return stub_flush_error; }
static int hfs_is_dirty(struct cnode *c) { (void)c; return 0; }
'''

suffix = r'''
static int failures;
static int run_case(const char *label, int mode, int isdir, int journal,
                    int global_full, int update_error, int metadata_error,
                    int flush_error, int expected, const char *expected_order,
                    int expected_flush_mode) {
    struct cnode cp = {0};
    struct filefork fp = {0};
    struct hfsmount h = {.jnl = journal ? &cp : NULL};
    struct vnode v = {.cp = &cp, .mp = &h, .fp = &fp, .isdir = isdir, .issystem = 0};
    always_do_fullfsync = global_full;
    stub_metadata_error = metadata_error;
    stub_flush_error = flush_error;
    stub_update_error = update_error;
    metadata_calls = flush_calls = update_calls = last_flush_mode = 0;
    order_n = 0; order[0] = '\0';
    int actual = hfs_fsync(&v, MNT_WAIT, mode, NULL);
    int failed = actual != expected || strcmp(order, expected_order) != 0 ||
        (metadata_calls != (strchr(expected_order, 'M') != NULL)) ||
        (flush_calls != (expected_flush_mode != 0)) ||
        (last_flush_mode != expected_flush_mode);
    printf("%s actual=%d expected=%d order=%s flush_mode=%d %s\n", label,
        actual, expected, order, last_flush_mode, failed ? "FAIL" : "PASS");
    failures += failed;
    return actual;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--baseline-negative-control") == 0) {
        run_case("baseline-known-missed-flush", HFS_FSYNC_FULL, 1, 0, 0,
                 0, 0, EIO, 0, "UMF", HFS_FLUSH_CACHE);
        return failures ? 1 : 0;
    }
    run_case("full-success", HFS_FSYNC_FULL, 1, 0, 0, 0, 0, 0, 0, "UMF", HFS_FLUSH_CACHE);
    run_case("full-metadata-failure", HFS_FSYNC_FULL, 1, 0, 0, 0, ENOSPC, 0, ENOSPC, "UMF", HFS_FLUSH_CACHE);
    run_case("full-cache-failure", HFS_FSYNC_FULL, 1, 0, 0, 0, 0, EIO, EIO, "UMF", HFS_FLUSH_CACHE);
    run_case("full-both-fail-metadata-precedes", HFS_FSYNC_FULL, 1, 0, 0, 0, ENOSPC, EIO, ENOSPC, "UMF", HFS_FLUSH_CACHE);
    run_case("barrier-nonjournal-cache-failure", HFS_FSYNC_BARRIER, 1, 0, 0, 0, 0, EIO, EIO, "UMF", HFS_FLUSH_CACHE);
    run_case("regular-file-cache-failure", HFS_FSYNC_FULL, 0, 0, 0, 0, 0, EIO, EIO, "UMF", HFS_FLUSH_CACHE);
    run_case("fullsync-sysctl-cache-failure", HFS_FSYNC, 1, 0, 1, 0, 0, EIO, EIO, "UMF", HFS_FLUSH_CACHE);
    run_case("ordinary-fsync-unchanged", HFS_FSYNC, 1, 0, 0, 0, ENOSPC, EIO, 0, "U", 0);
    run_case("journalled-full-unchanged", HFS_FSYNC_FULL, 1, 1, 0, 0, ENOSPC, EIO, 0, "UF", HFS_FLUSH_FULL);
    run_case("journalled-barrier-unchanged", HFS_FSYNC_BARRIER, 1, 1, 0, 0, ENOSPC, EIO, 0, "UF", HFS_FLUSH_JOURNAL_BARRIER);
    run_case("old-update-error-overwrite-unchanged", HFS_FSYNC_FULL, 1, 0, 0, EINVAL, 0, 0, 0, "UMF", HFS_FLUSH_CACHE);
    printf("SUMMARY cases=11 failures=%d\n", failures);
    return failures ? 1 : 0;
}
'''


def actual_function(path):
    source = path.read_text(encoding='latin-1')
    marker = '\nint\nhfs_fsync(struct vnode *vp, int waitfor, hfs_fsync_mode_t fsyncmode, struct proc *p)\n'
    assert source.count(marker) == 1
    start = source.index(marker) + 1
    end = source.index('\n\n/* Sync an hfs catalog b-tree node */', start)
    body = source[start:end]
    assert body.count('hfs_metasync_all(hfsmp)') == 1
    assert body.count('hfs_flush(hfsmp, HFS_FLUSH_CACHE)') == 1
    return body


def main():
    with tempfile.TemporaryDirectory(prefix='dali-hfs-fsync-') as tmp:
        work = Path(tmp)
        base = work / 'base/bsd/sweet_hfs/core/hfs_vnops.c'
        base.parent.mkdir(parents=True)
        base.write_bytes(BASE_BYTES)
        tree = work / 'tree'
        target = tree / 'bsd/sweet_hfs/core/hfs_vnops.c'
        target.parent.mkdir(parents=True)
        shutil.copyfile(base, target)
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(PATCH)],
                       cwd=tree, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == EXPECTED_AFTER
        compiler = shutil.which('clang') or shutil.which('gcc')
        if not compiler:
            raise SystemExit('host C compiler required')
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
                   UBSAN_OPTIONS='halt_on_error=1')
        for label, path in (('before', base), ('after', target)):
            generated = work / (label + '-actual-hfs-fsync.c')
            executable = work / (label + '-actual-hfs-fsync')
            generated.write_text(prefix + '\n' + actual_function(path) + '\n' + suffix)
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-g', '-O1',
                            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            str(generated), '-o', str(executable)], check=True)
            args = [str(executable)]
            if label == 'before':
                args.append('--baseline-negative-control')
            subprocess.run(args, check=True, env=env)
    print('PASS exact zero-fuzz 0208 replay and actual hfs_fsync host suite')
    print('Limit: stubs test error propagation only, not real VFS, metadata, storage, or device behavior.')


if __name__ == '__main__':
    main()
