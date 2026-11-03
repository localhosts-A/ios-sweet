#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "SweetUFSHSFloor.h"

struct mock {
 uint32_t reg[0x1000/4],cal[HSF_OPPS];
 unsigned reads,writes,smcs,acquires,releases,clock_checks,polls;
 unsigned fail_read,fail_smc,complete_at,fail_clock;
 uint64_t now;
 bool write_fail,release_fail,freeze,opaque_resource;
};
static int read32(void *p,uint32_t offset,uint32_t *value){
 struct mock *m=p;assert(!(offset&3) && offset<0x1000);++m->reads;
 if(m->reads==m->fail_read)return 1;
 if(offset==HSF_GEAR && m->writes){++m->polls;if(m->polls>=m->complete_at)m->reg[HSF_GEAR/4]=m->reg[HSF_REQUEST/4]<<4;}
 *value=m->reg[offset/4];return 0;
}
static int write32(void *p,uint32_t value){
 struct mock *m=p;assert(m->acquires==1 && !m->releases);++m->writes;assert(m->writes==1);
 uint32_t old=m->reg[HSF_REQUEST/4];assert((value&~HSF_REQUEST_MASK)==(old&~HSF_REQUEST_MASK));
 assert((value&HSF_REQUEST_MASK)>(old&HSF_REQUEST_MASK));if(m->write_fail)return 1;
 m->reg[HSF_REQUEST/4]=value;return 0;
}
static int smc(void *p,uint32_t cmd,uint32_t index,uint64_t *a0,uint64_t *a1){
 struct mock *m=p;++m->smcs;if(m->smcs==m->fail_smc)return 1;*a0=*a1=0;
 if(cmd==4){unsigned count=(m->reg[HSF_COUNT/4]&7u)+1u;assert(count<=HSF_OPPS && index<count && !m->acquires && !m->writes);*a1=m->cal[count-index-1];}
 else if(cmd==0x40){assert(!index && !m->acquires && !m->writes);++m->acquires;}
 else{assert(cmd==0x41 && !index && m->acquires==1 && !m->releases);++m->releases;if(m->release_fail)*a0=UINT64_MAX;}
 if(m->opaque_resource && cmd!=4)*a0=3; /* No zero-only resource ABI is assumed. */
 return 0;
}
static int clock_ok(void *p){struct mock *m=p;++m->clock_checks;return m->clock_checks==m->fail_clock;}
static uint64_t now_us(void *p){return ((struct mock *)p)->now;}
static void delay_us(void *p,uint32_t usec){struct mock *m=p;assert(usec<=2);if(!m->freeze)m->now+=usec;}
static const struct hsf_ops ops={read32,write32,smc,clock_ok,now_us,delay_us};
static void reset(struct hsf_state *s,struct mock *m){
 memset(s,0,sizeof(*s));memset(m,0,sizeof(*m));m->reg[HSF_COUNT/4]=5;
 const uint32_t cal[]={575000,600000,650000,725000,825000,875000};memcpy(m->cal,cal,sizeof(cal));
 m->reg[HSF_REQUEST/4]=0x52000;m->complete_at=2;m->now=1;
}
int main(void){struct hsf_state s;struct mock m;
 reset(&s,&m);assert(!hsf_acquire(&s,false,NULL,NULL) && !m.reads && !m.smcs && !m.writes);
 assert(hsf_validate(&s)==HSF_STATE);
 reset(&s,&m);assert(!hsf_acquire(&s,true,&ops,&m));assert(s.phase==HSF_ACTIVE && s.request==2 && s.nominal_uv==650000 && s.calibrated_uv==650000);
 assert(m.writes==1 && m.acquires==1 && m.releases==1 && (m.reg[HSF_REQUEST/4]&~HSF_REQUEST_MASK)==0x52000);
 assert(!hsf_validate(&s) && m.writes==1 && m.smcs==8);
 assert(hsf_acquire(&s,true,&ops,&m)==HSF_STATE && m.writes==1);
 reset(&s,&m);m.opaque_resource=true;assert(!hsf_acquire(&s,true,&ops,&m));
 assert(s.resource_request_result==3 && s.resource_release_result==3 && !s.resource_requested);
 reset(&s,&m);m.reg[HSF_REQUEST/4]|=5u<<4;m.reg[HSF_GEAR/4]=5u<<8;
 assert(!hsf_acquire(&s,true,&ops,&m) && !m.writes && s.request==5 && m.releases==1);
 reset(&s,&m);m.cal[2]=640000;
 assert(!hsf_acquire(&s,true,&ops,&m) && s.request==3 && s.nominal_uv==725000 && s.calibrated_uv==725000);
 for(unsigned count=3;count<=HSF_OPPS;count++){
  reset(&s,&m);m.reg[HSF_COUNT/4]=count-1;
  assert(!hsf_acquire(&s,true,&ops,&m) && s.opp_count==count && s.raw_count==count-1);
  assert(s.request==2 && s.calibrated_uv==650000 && m.smcs==count+2 && m.writes==1);
 }
 for(unsigned count=1;count<=8;count++){
  if(count>=3 && count<=HSF_OPPS)continue;
  reset(&s,&m);m.reg[HSF_COUNT/4]=count-1;
  assert(hsf_acquire(&s,true,&ops,&m)==HSF_COUNT_ERROR && !m.writes && !m.smcs);
 }
 reset(&s,&m);m.reg[HSF_COUNT/4]=2;m.cal[2]=640000;
 assert(hsf_acquire(&s,true,&ops,&m)==HSF_TABLE && !m.writes && !m.acquires);
 reset(&s,&m);m.reg[HSF_COUNT/4]=2;m.reg[HSF_REQUEST/4]|=3u<<4;
 assert(hsf_acquire(&s,true,&ops,&m)==HSF_REQUEST_ERROR && !m.writes && !m.acquires);
 for(unsigned bad=0;bad<4;bad++){
  reset(&s,&m);if(bad==0)m.reg[HSF_COUNT/4]=1;if(bad==1)m.reg[HSF_FORCE_REQUEST/4]=UINT32_MAX;
  if(bad==2)m.reg[HSF_HALT/4]=2;if(bad==3)m.cal[3]=1;
  assert(hsf_acquire(&s,true,&ops,&m)!=0 && !m.writes && !m.acquires && s.phase==HSF_FAILED);
  unsigned reads=m.reads;assert(hsf_acquire(&s,true,&ops,&m)!=0 && m.reads==reads);
 }
 for(unsigned fail=1;fail<=7;fail++){
  reset(&s,&m);m.fail_smc=fail;assert(hsf_acquire(&s,true,&ops,&m)!=0 && !m.writes && s.phase==HSF_FAILED);
 }
 reset(&s,&m);m.write_fail=true;
 assert(hsf_acquire(&s,true,&ops,&m)==HSF_WRITE && m.writes==1 && m.releases==1);
 reset(&s,&m);m.release_fail=true;
 assert(hsf_acquire(&s,true,&ops,&m)==HSF_RELEASE && m.writes==1 && m.releases==1 && s.resource_requested);
 reset(&s,&m);m.complete_at=100000;
 assert(hsf_acquire(&s,true,&ops,&m)==HSF_TIMEOUT && m.writes==1 && m.releases==1 && m.polls==1001);
 reset(&s,&m);m.freeze=true;m.complete_at=100000;
 assert(hsf_acquire(&s,true,&ops,&m)==HSF_TIMEOUT && m.writes==1 && m.releases==1 && m.polls==1001);
 for(unsigned which=0;which<5;which++){
  reset(&s,&m);assert(!hsf_acquire(&s,true,&ops,&m));
  if(which==0)m.reg[HSF_REQUEST/4]&=~HSF_REQUEST_MASK;
  if(which==1)m.reg[HSF_GEAR/4]=0;
  if(which==2)m.reg[HSF_TARGET/4]|=1u<<16;
  if(which==3)m.reg[HSF_FORCE_REQUEST/4]=1;
  if(which==4)m.fail_clock=m.clock_checks+1;
  /* Disable the completion mock so it cannot hide an injected hardware loss. */
  m.writes=0;assert(hsf_validate(&s)!=0 && s.phase==HSF_FAILED);
  unsigned reads=m.reads;assert(hsf_validate(&s)!=0 && m.reads==reads);
 }
 puts("PASS actual AP minimum request: calibration, retained vote, resource pairing, no downgrade/retry, timeout and later loss");
 return 0;}
