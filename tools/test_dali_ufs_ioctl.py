#!/usr/bin/env python3
"""Compile the actual UFS ioctl and HFS journal limit functions in a host shim."""
import pathlib
import re
import subprocess
import sys
import tempfile

source = pathlib.Path(sys.argv[1]).read_text()
xnu = pathlib.Path(sys.argv[2])
disk = (xnu / 'bsd/sys/disk.h').read_text()
hfs = (xnu / 'bsd/sweet_hfs/core/hfs_journal.c').read_text()
vfs = (xnu / 'bsd/vfs/vfs_subr.c').read_text()

def extract(text, marker):
    start = text.index(marker)
    end = text.index('\n}\n', start) + 3
    return text[start:end]

names = '''DKIOCGETBLOCKSIZE DKIOCSETBLOCKSIZE DKIOCGETPHYSICALBLOCKSIZE
DKIOCGETBLOCKCOUNT DKIOCISWRITABLE DKIOCSYNCHRONIZE DKIOCSYNCHRONIZECACHE
DKIOCGETFEATURES DKIOCGETMAXBLOCKCOUNTREAD DKIOCGETMAXBLOCKCOUNTWRITE
DKIOCGETMAXBYTECOUNTREAD DKIOCGETMAXBYTECOUNTWRITE DKIOCGETMAXSEGMENTCOUNTREAD
DKIOCGETMAXSEGMENTCOUNTWRITE DKIOCGETMAXSEGMENTBYTECOUNTREAD
DKIOCGETMAXSEGMENTBYTECOUNTWRITE DKIOCGETMINSEGMENTALIGNMENTBYTECOUNT
DKIOCGETCOMMANDPOOLSIZE DK_FEATURE_FORCE_UNIT_ACCESS DK_FEATURE_UNMAP
DK_FEATURE_BARRIER DKIOCGETTHROTTLEMASK DKIOCISVIRTUAL DKIOCISSOLIDSTATE
DKIOCGETIOMINSATURATIONBYTECOUNT DKIOCCORESTORAGE DKIOCGETAPFSFLAVOUR
DKIOCGETLOCATION DK_LOCATION_EXTERNAL DK_CORESTORAGE_PIN_YOUR_METADATA'''.split()
macros = []
for name in names:
    matches = re.findall(r'^#define\s+' + name + r'\s+[^\n]+', disk, re.M)
    if len(matches) != 1:
        raise ValueError((name, matches))
    macros.append(matches[0])

prefix = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>
#include <sys/types.h>
#ifndef __unused
#define __unused __attribute__((__unused__))
#endif
#define IOC_VOID 0x20000000u
#define IOC_OUT  0x40000000u
#define IOC_IN   0x80000000u
#define _IOC(inout,group,num,len) ((inout) | (((len) & 0x1fff) << 16) | ((group) << 8) | (num))
#define _IO(g,n) _IOC(IOC_VOID,(g),(n),0)
#define _IOR(g,n,t) _IOC(IOC_OUT,(g),(n),sizeof(t))
#define _IOW(g,n,t) _IOC(IOC_IN,(g),(n),sizeof(t))
typedef struct {uint64_t offset,length; uint32_t options; uint8_t reserved[4];} dk_synchronize_t;
typedef struct {uint64_t flags,hotfile_size,hibernate_minsize,swapfile_pinning,padding[4];} dk_corestorage_info_t;
typedef int dk_apfs_flavour_t;
#define DK_APFS_FUSION 1
#define UFS_BLOCK 4096u
#define BOUNCE_SIZE (1u << 20)
#define DEV_BSIZE 512u
struct proc {int unused;};
static struct { uint32_t secsize; uint64_t nblocks; bool writable; } su;
static int sweet_ufs_lock, held, locks, unlocks, syncs, sync_error;
static void lck_mtx_lock(int *p) {assert(p == &sweet_ufs_lock && !held); held=1; ++locks;}
static void lck_mtx_unlock(int *p) {assert(p == &sweet_ufs_lock && held); held=0; ++unlocks;}
static int ufs_sync_cache(void) {assert(held); ++syncs; return sync_error;}
'''
bridge = r'''
struct vnode {int unused;};
struct vfs_context {int unused;};
typedef struct {uint32_t flags,max_read_size,max_write_size;} journal;
#define JOURNAL_DO_FUA_WRITES 1
#define JOURNAL_USE_UNMAP 2
#define JOURNAL_FEATURE_BARRIER 4
static const char *vnode_getname_printable(struct vnode *vp) {(void)vp;return "mock";}
static void vnode_putname_printable(const char *p) {(void)p;}
#define VNOP_IOCTL(vp,cmd,data,flag,ctx) ((void)(vp),(void)(ctx),sweet_ufs_ioctl(0,(cmd),(data),(flag),NULL,0))
typedef struct vnode *vnode_t;
typedef struct vfs_context *vfs_context_t;
typedef int boolean_t;
typedef struct mount {
 uint64_t mnt_throttle_mask; uint32_t mnt_devbsdunit,mnt_devblocksize;
 uint32_t mnt_maxreadcnt,mnt_maxwritecnt,mnt_maxsegreadsize,mnt_maxsegwritesize;
 uint16_t mnt_segreadcnt,mnt_segwritecnt;
 uint32_t mnt_alignmentmask,mnt_ioqueue_depth,mnt_ioscale,mnt_minsaturationbytecount;
 uint32_t mnt_kern_flag,mnt_flag,mnt_ioflags;
} *mount_t;
static vnode_t rootvp;
static uint32_t rootunit;
static unsigned num_trailing_0(uint64_t x) {return x ? __builtin_ctzll(x) : 64;}
static void set_fsblocksize(vnode_t v) {(void)v;}
static vfs_context_t vfs_context_current(void) {static struct vfs_context c; return &c;}
#define TRUE 1
#define FALSE 0
#define PAGE_MASK (PAGE_SIZE-1)
#define MAX_UPL_SIZE_BYTES (64u*1024u*1024u)
#define MNT_DEFAULT_IOQUEUE_DEPTH 32
#define MNT_IOSCALE(n) (((n)+31)/32)
#define MNTK_ROOTDEV 1
#define MNTK_VIRTUALDEV 2
#define MNTK_SSD 4
#define MNT_REMOVABLE 8
#define MNT_IOFLAGS_FUA_SUPPORTED 1
#define MNT_IOFLAGS_UNMAP_SUPPORTED 2
#define MNT_IOFLAGS_CSUNMAP_SUPPORTED 4
#define MNT_IOFLAGS_FUSION_DRIVE 8
#define MNT_IOFLAGS_PERIPHERAL_DRIVE 16
#define CONFIG_IOSCHED 0
'''
test = r'''
static uint64_t q64(unsigned long cmd) {
 struct {uint64_t before,value,after;} v={0x123456789abcdef0ull,UINT64_MAX,0xfedcba9876543210ull};
 assert(((cmd>>16)&0x1fff)==8 && !sweet_ufs_ioctl(0,cmd,(char *)&v.value,0,NULL,0));
 assert(v.before==0x123456789abcdef0ull && v.after==0xfedcba9876543210ull); return v.value;
}
static uint32_t q32(unsigned long cmd) {
 struct {uint32_t before,value,after;} v={0x12345678u,UINT32_MAX,0x87654321u};
 assert(((cmd>>16)&0x1fff)==4 && !sweet_ufs_ioctl(0,cmd,(char *)&v.value,0,NULL,0));
 assert(v.before==0x12345678u && v.after==0x87654321u); return v.value;
}
int main(void) {
 _Static_assert(sizeof(dk_synchronize_t)==24,"synchronize ABI");
 _Static_assert(DKIOCGETFEATURES==0x4004644c,"feature ABI");
 _Static_assert(DKIOCGETMAXBYTECOUNTREAD==0x40086446,"byte count ABI");
 _Static_assert(DKIOCGETCOMMANDPOOLSIZE==0x4004644e,"pool ABI");
 /* A <=1 MiB request can cross one extra page when not page-aligned. */
 const unsigned lengths[]={1,PAGE_SIZE-1,PAGE_SIZE,PAGE_SIZE+1,BOUNCE_SIZE-1,BOUNCE_SIZE};
 for (unsigned off=0;off<PAGE_SIZE;++off) for (unsigned i=0;i<sizeof(lengths)/sizeof(lengths[0]);++i) {
  unsigned pages=(off+lengths[i]+PAGE_SIZE-1)/PAGE_SIZE;
  assert(pages<=BOUNCE_SIZE/PAGE_SIZE+1);
  if (off && lengths[i]==BOUNCE_SIZE) assert(pages==BOUNCE_SIZE/PAGE_SIZE+1);
 }
 for (unsigned ro=0;ro<2;ro++) for (unsigned s=512;s<=4096;s*=2) {
  su.secsize=s; su.nblocks=8192; su.writable=!ro;
  assert(q32(DKIOCGETFEATURES)==0 && q32(DKIOCGETCOMMANDPOOLSIZE)==1);
  assert(q32(DKIOCGETBLOCKSIZE)==s && q32(DKIOCGETPHYSICALBLOCKSIZE)==4096);
  assert(q32(DKIOCISWRITABLE)==!ro && q64(DKIOCGETBLOCKCOUNT)==8192ull*4096/s);
  assert(q64(DKIOCGETMAXBLOCKCOUNTREAD)==BOUNCE_SIZE/s);
  assert(q64(DKIOCGETMAXBLOCKCOUNTWRITE)==(ro?0:BOUNCE_SIZE/s));
  assert(q64(DKIOCGETMAXBYTECOUNTREAD)==BOUNCE_SIZE);
  assert(q64(DKIOCGETMAXBYTECOUNTWRITE)==(ro?0:BOUNCE_SIZE));
  assert(q64(DKIOCGETMAXSEGMENTCOUNTREAD)==BOUNCE_SIZE/PAGE_SIZE+1);
  assert(q64(DKIOCGETMAXSEGMENTCOUNTWRITE)==BOUNCE_SIZE/PAGE_SIZE+1);
  assert(q64(DKIOCGETMAXSEGMENTBYTECOUNTREAD)==BOUNCE_SIZE);
  assert(q64(DKIOCGETMAXSEGMENTBYTECOUNTWRITE)==BOUNCE_SIZE);
  assert(q64(DKIOCGETMINSEGMENTALIGNMENTBYTECOUNT)==1);
  journal j={0}; struct vnode v={0}; struct vfs_context c={0};
  get_io_info(&v,4096,&j,&c);
  assert(j.flags==0 && j.max_read_size==BOUNCE_SIZE);
  assert(j.max_write_size==(ro?128*1024:BOUNCE_SIZE));
  struct mount m={0}; rootvp=&v;
  assert(vfs_init_io_attributes(&v,&m)==0); /* optional queries are ignored */
  assert(m.mnt_devblocksize==s && m.mnt_maxreadcnt==BOUNCE_SIZE);
  assert(m.mnt_maxwritecnt==(ro?MAX_UPL_SIZE_BYTES:BOUNCE_SIZE));
  assert(m.mnt_segreadcnt==BOUNCE_SIZE/PAGE_SIZE+1 && m.mnt_segwritecnt==m.mnt_segreadcnt);
  assert(m.mnt_maxsegreadsize==BOUNCE_SIZE && m.mnt_maxsegwritesize==BOUNCE_SIZE);
  assert(m.mnt_alignmentmask==0 && m.mnt_ioqueue_depth==32 && m.mnt_ioflags==0);
 }
 su.secsize=512; su.writable=true; uint32_t val=1024;
 assert(!sweet_ufs_ioctl(0,DKIOCSETBLOCKSIZE,(char *)&val,0,NULL,0) && su.secsize==1024);
 val=1024; assert(sweet_ufs_ioctl(0,DKIOCSETBLOCKSIZE,(char *)&val,0,NULL,1)==ENODEV);
 val=513; assert(sweet_ufs_ioctl(0,DKIOCSETBLOCKSIZE,(char *)&val,0,NULL,0)==EINVAL && su.secsize==1024);
 uint64_t data=0; sync_error=EIO;
 assert(sweet_ufs_ioctl(0,DKIOCSYNCHRONIZECACHE,(char *)&data,0,NULL,0)==EIO);
 assert(locks==1 && unlocks==1 && syncs==1 && !held);
 su.writable=false;
 assert(!sweet_ufs_ioctl(0,DKIOCSYNCHRONIZE,(char *)&data,0,NULL,0));
 assert(locks==1 && unlocks==1 && syncs==1);
 assert(sweet_ufs_ioctl(0,0x12345678,(char *)&data,0,NULL,0)==ENOTTY);
 puts("PASS"); return 0;
}
'''
actual_ioctl = extract(source, 'static int\nsweet_ufs_ioctl(')
actual_hfs = extract(hfs, 'static void\nget_io_info(')
actual_vfs = extract(vfs, 'errno_t\nvfs_init_io_attributes(').replace('errno_t\nvfs_init_io_attributes(', 'int\nvfs_init_io_attributes(', 1)
for page in (4096, 16384):
    code = prefix + '\n#define PAGE_SIZE ' + str(page) + 'u\n' + '\n'.join(macros) + '\n' + actual_ioctl + bridge + actual_hfs + actual_vfs + test
    with tempfile.TemporaryDirectory() as temp:
        c = pathlib.Path(temp) / 'fixture.c'
        exe = pathlib.Path(temp) / 'fixture'
        c.write_text(code)
        subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(c),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
    print('PAGE_SIZE=%d: real ioctl, HFS journal and VFS attribute functions passed' % page, flush=True)
