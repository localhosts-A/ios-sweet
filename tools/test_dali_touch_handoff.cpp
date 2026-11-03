#include "DaliTouchHandoff.h"
#include <cassert>
#include <cstring>
#include <cstdio>
struct Fixture {
    unsigned int opened=0, reads=0, closes=0, failOpen=99, failRead=99;
    static bool open(void *p,unsigned int i,unsigned long long address) {
        Fixture *f=static_cast<Fixture *>(p); const unsigned long long expected[]={0x1002d000,0x10000000,0x14500000,0x16640000};
        assert(i==f->opened && address==expected[i]); if(i==f->failOpen)return false; ++f->opened; return true;
    }
    static bool read(void *p,unsigned int region,unsigned int off,unsigned int *v) {
        Fixture *f=static_cast<Fixture *>(p); const unsigned int regions[]={0,1,1,2,2,2,2,3};
        const unsigned int offsets[]={0x3a0,0x90,0x274,0x28,0x2c14,0x08,0x2c04,0x14};
        unsigned i=f->reads++; assert(i<8 && region==regions[i] && off==offsets[i]);
        if(i==f->failRead)return false; *v=0x100+i;return true;
    }
    static void close(void *p,unsigned int i) {Fixture*f=static_cast<Fixture *>(p); assert(i==f->opened-1-f->closes);++f->closes;}
};
static int run(Fixture &f,const void *c,unsigned int n,const void *e,unsigned int en,DaliTouchSnapshot &s) {
    DaliTouchIO io={&f,Fixture::open,Fixture::read,Fixture::close}; return dali_touch_handoff(c,n,e,en,&io,&s);
}
int main(){
    const char dali[]="D22AP\0xiaomi,dali\0";const char other[]="xiaomi,sweet\0";const unsigned char on[]={1,0,0,0},off[]={0,0,0,0};
    DaliTouchSnapshot s={}; Fixture f;
    assert(run(f,other,sizeof(other),on,4,s)==0 && !f.opened && !f.reads);
    assert(run(f,dali,sizeof(dali),off,4,s)==0 && !f.opened && !f.reads);
    assert(run(f,dali,sizeof(dali),on,3,s)==0 && !f.opened);
    assert(run(f,dali,6,on,4,s)==0 && !f.opened);
    assert(run(f,dali,sizeof(dali),on,4,s)==1 && f.opened==4 && f.reads==8 && f.closes==4);
    assert(s.gpio==0x100 && s.mux==0x101 && s.fence==0x102 && s.gateStatus==0x107);
    for(unsigned i=0;i<4;i++){Fixture t;t.failOpen=i;DaliTouchSnapshot a={}; assert(run(t,dali,sizeof(dali),on,4,a)==-1);assert(t.opened==i&&t.closes==i&&!t.reads);}
    for(unsigned i=0;i<8;i++){Fixture t;t.failRead=i;DaliTouchSnapshot a={};assert(run(t,dali,sizeof(dali),on,4,a)==-1);assert(t.opened==4&&t.closes==4&&t.reads==i+1);assert(!a.gpio&&!a.mux);}
    puts("actual Dali touch handoff: disabled, non-Dali, whitelist and cleanup PASS");
}
