#!/usr/bin/env python3
"""Strict private diagnostic-record parser; does not establish runtime truth."""
import re

FIELDS={
 'SWEET BBCTXS': 'v s stat mach sloss mloss final',
 'SWEET BBCTXC': 'v s k begun done full re stale retired exhausted open',
 'SWEET BBCTX': 'v s k p id iv th e q vld b z rk r kn ps pn nc nk ne',
 'SWEET BBCTXM': 'v s e hex',
 'SWEET BBCTXP': 'v s e off n ps hex',
 'SWEET BBCTXU': 'v s k n p id iv th t r link',
 'SWEET BBCTXEND': 'v s lines bytes dropped',
}
HEX=re.compile(r'^[0-9a-f]*$')
def parse(line):
 if not isinstance(line,str) or not line.endswith('\n') or '\n' in line[:-1] or len(line.encode('utf-8'))>255:raise ValueError('incomplete or oversized')
 name,sep,body=line[:-1].partition(': ')
 if not sep or name not in FIELDS:raise ValueError('unknown prefix')
 pairs=[x.split('=',1) for x in body.split(' ')]
 if any(len(x)!=2 or not x[0] for x in pairs):raise ValueError('malformed fields')
 raw=dict(pairs)
 if len(raw)!=len(pairs):raise ValueError('duplicate fields')
 fields=set(FIELDS[name].split())
 if set(raw)!=fields:raise ValueError('fields')
 data={}
 for k,v in raw.items():
  if k in ('k','hex','link'):data[k]=v;continue
  try:n=int(v,0)
  except ValueError:raise ValueError('number') from None
  if k in ('r','ne'):
   if not -(1<<31)<=n<(1<<31):raise ValueError('signed range')
  elif not 0<=n<1<<64:raise ValueError('unsigned range')
  data[k]=n
 if data['v']!=1 or not 0<=data['s']<40:raise ValueError('version/sample')
 if 'k' in data and data['k'] not in (('S','M','E') if name=='SWEET BBCTX' else ('S','M')):raise ValueError('kind')
 for k in ('p','iv','pn','nc','off','n'):
  if k in data and data[k]>=1<<32 and not (name=='SWEET BBCTXU' and k=='n'):raise ValueError('field width')
 for k in ('vld','kn','nk','stat','mach','sloss','mloss','final','exhausted','open','incomplete'):
  if k in data and data[k] not in (0,1):raise ValueError('boolean')
 if name=='SWEET BBCTX':
  if data['q'] not in (1,2,3) or data['ps'] not in (0,1,2,3) or not 0<=data['pn']<=192 or data['rk'] not in range(7):raise ValueError('record state')
  if data['q']!=2 and data['kn']!=0:raise ValueError('uncompleted result')
  # A single line cannot prove its same-sample status was retained. Callers
  # must additionally check BBCTXS snapshot, loss, and dropped lines.
  data['result_observed']=bool(data['q']==2 and data['kn'])
  data['validity']='requires-status' if data['vld'] else 'loss'
 if name=='SWEET BBCTXU' and data['link']!='unknown':raise ValueError('unpaired relation')
 if name=='SWEET BBCTXM':
  words=data['hex'].split(',')
  if len(words)!=7 or any(not x or len(x)>16 or not HEX.fullmatch(x) for x in words):raise ValueError('mach words')
  data['words']=[int(x,16) for x in words]
 if name=='SWEET BBCTXP':
  h=data['hex']; n=data['n']; off=data['off']
  if data['ps'] not in (1,2) or not 0<=n<=48 or not 0<=off<=192 or off+n>192 or len(h)!=2*n or not HEX.fullmatch(h):raise ValueError('path bytes')
  data['bytes']=bytes.fromhex(h)
 return name,data

def parse_round(lines):
 """Validate one complete extracted round, not the completeness of a whole boot."""
 parsed=[parse(line) for line in lines]
 if len(parsed)<2 or parsed[0][0]!='SWEET BBCTXS' or parsed[-1][0]!='SWEET BBCTXEND':raise ValueError('missing round framing')
 status=parsed[0][1];end=parsed[-1][1];sample=status['s']
 if any(item[1]['s']!=sample for item in parsed):raise ValueError('mixed samples')
 if any(item[0] in ('SWEET BBCTXS','SWEET BBCTXEND') for item in parsed[1:-1]):raise ValueError('duplicate framing')
 if end['lines']!=len(lines)-1 or end['bytes']!=sum(len(x.encode('utf-8')) for x in lines[:-1]):raise ValueError('round loss')
 if end['dropped']!=0:raise ValueError('producer dropped records')
 for name,data in parsed[1:-1]:
  if name!='SWEET BBCTX':continue
  domain='mach' if data['k']=='M' else 'stat';loss='mloss' if domain=='mach' else 'sloss'
  if not status[domain]:raise ValueError('record without snapshot')
  if data['vld'] and status[loss]:raise ValueError('contradictory loss status')
  data['validity']='recorded' if data['vld'] and not status[loss] else 'loss'
 return parsed
