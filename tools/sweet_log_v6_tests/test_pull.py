#!/usr/bin/env python3
"""Test actual retrieval script against a mock adb; never touches a device."""
from pathlib import Path
import os
import struct
import subprocess
import tempfile
import unittest

D = Path(__file__).resolve().parents[1]
SCRIPT = D/'tools/sweet-log-pull.sh'
MOCK = '''#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$ADB_CALLS"
if [ "$1" = -s ]; then shift 2; fi
case "$1" in
 shell)
  if [ "$2" = blockdev ]; then printf '%s\\n' "$ADB_BYTES"; else printf '/dev/mock\\n'; fi ;;
 exec-out)
  shift
  /usr/bin/python3 -I - "$@" <<'PY'
import os, struct, sys, zlib
assert sys.argv[1:4] == ['dd','if=/dev/mock','bs=4096']
assert sys.argv[5:] == ['count=1025','status=none']
n=int(os.environ.get('MOCK_SHORT','4198400'))
seq=int(sys.argv[4].split('=',1)[1]); data=bytearray(n)
if n >= 4100:
 struct.pack_into('<8sIIQQ',data,0,b'SWEETLOG',1,4194304,4,seq)
 if (os.environ.get('MOCK_IDS')=='duplicate' and seq in (5121,6146)) or (os.environ.get('MOCK_IDS')=='single' and seq==5121):
  data[32:40]=b'SWIDv001';data[40:72]=b'0123456789abcdef0123456789abcdef'
  struct.pack_into('<I',data,72,zlib.crc32(data[:72]))
 data[4096:4100]=b'test'
sys.stdout.buffer.write(data)
PY
  ;;
 *) exit 77 ;;
esac
'''

class TestPull(unittest.TestCase):
 def run_cmd(self,args,bytes_=33554432,short=None):
  with tempfile.TemporaryDirectory(prefix='sweetlog-pull-') as t:
   p=Path(t); (p/'adb').write_text(MOCK); (p/'adb').chmod(0o755)
   env=dict(os.environ, PATH=str(p)+':'+os.environ['PATH'], ADB_CALLS=str(p/'calls'),ADB_BYTES=str(bytes_))
   if short is not None:env['MOCK_SHORT']=str(short)
   output=p/'result.txt'
   r=subprocess.run(['sh',str(SCRIPT),*args,str(output)],env=env,capture_output=True,text=True,timeout=20)
   calls=(p/'calls').read_text() if (p/'calls').exists() else ''
   text=output.read_text() if output.exists() else None
   binary=(p/'result.txt.bin').stat().st_size if (p/'result.txt.bin').exists() else None
   return r,calls,text,binary
 def test_three_exact_slot_offsets(self):
  for slot,skip in enumerate((4096,5121,6146)):
   r,calls,text,binary=self.run_cmd(['-s','fake','--slot',str(slot)])
   self.assertEqual(r.returncode,0,r.stderr);self.assertIn('skip='+str(skip),calls)
   self.assertEqual(text,'test');self.assertEqual(binary,4198400)
 def test_no_unsafe_or_short_read(self):
  r,calls,_,_=self.run_cmd(['--slot','3']);self.assertNotEqual(r.returncode,0);self.assertEqual(calls,'')
  r,calls,_,_=self.run_cmd(['--slot','2'],bytes_=7170*4096);self.assertNotEqual(r.returncode,0);self.assertNotIn('exec-out',calls)
  r,calls,_,binary=self.run_cmd(['--slot','1'],short=10);self.assertNotEqual(r.returncode,0);self.assertEqual(binary,10)
  r,calls,_,_=self.run_cmd(['-p','userdata','--slot','2']);self.assertNotEqual(r.returncode,0);self.assertNotIn('exec-out',calls)
 def test_explicit_slot_and_empty_identity(self):
  r,calls,_,_=self.run_cmd([]);self.assertNotEqual(r.returncode,0);self.assertEqual(calls,'')
  r,calls,_,_=self.run_cmd(['--all','--expect-run-id','']);self.assertNotEqual(r.returncode,0);self.assertEqual(calls,'')
  r,calls,_,_=self.run_cmd(['--slot','0','--expect-run-id','']);self.assertNotEqual(r.returncode,0);self.assertEqual(calls,'')
  r,calls,text,_=self.run_cmd(['-p','userdata','--slot','0'])
  self.assertEqual(r.returncode,0,r.stderr);self.assertIn('skip=7167',calls);self.assertEqual(text,'test')
 def test_all_slots_and_no_clobber(self):
  with tempfile.TemporaryDirectory(prefix='sweetlog-pull-all-') as t:
   p=Path(t);(p/'adb').write_text(MOCK);(p/'adb').chmod(0o755)
   env=dict(os.environ,PATH=str(p)+':'+os.environ['PATH'],ADB_CALLS=str(p/'calls'),ADB_BYTES='33554432')
   out=p/'capture';rid='0123456789abcdef0123456789abcdef'
   r=subprocess.run(['sh',str(SCRIPT),'--all','--expect-run-id',rid,str(out)],env=env,capture_output=True,text=True)
   self.assertEqual(r.returncode,4,r.stderr);self.assertIn('run-id-status no-header-match',r.stdout)
   calls=(p/'calls').read_text()
   for i,skip in enumerate((4096,5121,6146)):
    self.assertIn('skip='+str(skip),calls);self.assertEqual((p/f'capture.slot{i}.bin').stat().st_size,4198400)
   self.assertEqual(r.stdout.count('legacy-unattributed'),3)
   self.assertEqual(r.stdout.count('"payload_integrity": "unverified"'),3)
   (p/'calls').unlink();r=subprocess.run(['sh',str(SCRIPT),'--all',str(out)],env=env,capture_output=True,text=True)
   self.assertNotEqual(r.returncode,0);self.assertNotIn('exec-out',(p/'calls').read_text())
   old=p/'old.txt';old.write_text('previous');(p/'calls').unlink()
   r=subprocess.run(['sh',str(SCRIPT),'--slot','0',str(old)],env=env,capture_output=True,text=True)
   self.assertNotEqual(r.returncode,0);self.assertEqual(old.read_text(),'previous')
   self.assertFalse((p/'old.txt.bin').exists());self.assertNotIn('exec-out',(p/'calls').read_text())
 def test_single_match_and_no_expected_id(self):
  with tempfile.TemporaryDirectory(prefix='sweetlog-pull-single-') as t:
   p=Path(t);(p/'adb').write_text(MOCK);(p/'adb').chmod(0o755)
   env=dict(os.environ,PATH=str(p)+':'+os.environ['PATH'],ADB_CALLS=str(p/'calls'),ADB_BYTES='33554432',MOCK_IDS='single')
   out=p/'capture';rid='0123456789abcdef0123456789abcdef'
   r=subprocess.run(['sh',str(SCRIPT),'--all','--expect-run-id',rid,str(out)],env=env,capture_output=True,text=True)
   self.assertEqual(r.returncode,5,r.stderr);self.assertEqual(r.stdout.count('"match": true'),1)
   self.assertIn('image-id-only; boot unverified; payload unverified',r.stdout)
   for i in range(3):self.assertEqual((p/f'capture.slot{i}.bin').stat().st_size,4198400)
   single=p/'single.txt'
   r=subprocess.run(['sh',str(SCRIPT),'--slot','1','--expect-run-id',rid,str(single)],env=env,capture_output=True,text=True)
   self.assertEqual(r.returncode,5,r.stderr);self.assertIn('other slots not checked',r.stdout)
   self.assertEqual(single.read_text(),'test');self.assertEqual((p/'single.txt.bin').stat().st_size,4198400)
   r=subprocess.run(['sh',str(SCRIPT),'--all',str(p/'unattributed')],env=env,capture_output=True,text=True)
   self.assertEqual(r.returncode,0,r.stderr);self.assertNotIn('run-id-status',r.stdout)
 def test_duplicate_run_id_is_ambiguous(self):
  with tempfile.TemporaryDirectory(prefix='sweetlog-pull-duplicate-') as t:
   p=Path(t);(p/'adb').write_text(MOCK);(p/'adb').chmod(0o755)
   env=dict(os.environ,PATH=str(p)+':'+os.environ['PATH'],ADB_CALLS=str(p/'calls'),ADB_BYTES='33554432',MOCK_IDS='duplicate')
   out=p/'capture';rid='0123456789abcdef0123456789abcdef'
   r=subprocess.run(['sh',str(SCRIPT),'--all','--expect-run-id',rid,str(out)],env=env,capture_output=True,text=True)
   self.assertEqual(r.returncode,3,r.stderr);self.assertIn('ambiguous matches=2; no slot selected',r.stdout)
   self.assertEqual(r.stdout.count('"match": true'),2)
   for i in range(3):self.assertEqual((p/f'capture.slot{i}.bin').stat().st_size,4198400)

if __name__ == '__main__': unittest.main(verbosity=2)
