#!/usr/bin/env python3
"""Validate geometry, provenance and damage classifications; no device access."""
from pathlib import Path
import runpy
import struct
import subprocess
import tempfile
import unittest
import zlib
D=Path(__file__).resolve().parents[1]
SCRIPT=D/'tools/sweet-log.py'
m=runpy.run_path(str(SCRIPT));inspect=m['inspect'];AREA=m['AREA'];RING=m['RING'];BLOCK=m['BLOCK']
RID='0123456789abcdef0123456789abcdef'
def make():
 b=bytearray(AREA);struct.pack_into('<8sIIQQ',b,0,b'SWEETLOG',1,RING,4,1);b[BLOCK:BLOCK+4]=b'test';return b
def identify(b):
 b[32:40]=b'SWIDv001';b[40:72]=RID.encode();struct.pack_into('<I',b,72,zlib.crc32(b[:72]));return b
def identify_v2(b,chunks,byte_count):
 b[32:40]=b'SWIDv002';b[40:72]=RID.encode();struct.pack_into('<QQ',b,72,chunks,byte_count)
 struct.pack_into('<I',b,88,zlib.crc32(b[:88]));return b
class TestDecoder(unittest.TestCase):
 def test_classifications(self):
  self.assertEqual(inspect(b'')['classification'],'invalid-length')
  self.assertEqual(inspect(bytearray(AREA))['classification'],'empty')
  b=make();self.assertEqual(inspect(b)['classification'],'legacy-unattributed')
  self.assertIsNone(inspect(b,RID)['match'])
  for field,value in ((8,2),(12,0),(12,1),(16,0),(24,0)):
   b=make();struct.pack_into('<I' if field in (8,12) else '<Q',b,field,value)
   self.assertEqual(inspect(b)['classification'],'invalid-header')
  b=make();b[0]=0;self.assertEqual(inspect(b)['classification'],'partial-or-unknown')
  b=make();b[40]=1;self.assertEqual(inspect(b)['classification'],'partial-or-unknown-extension')
  b=make();b[32]=1;self.assertEqual(inspect(b)['classification'],'unknown-or-torn-extension')
  b=identify(make());self.assertEqual(inspect(b,RID)['classification'],'identified-header')
  self.assertTrue(inspect(b,RID)['match']);self.assertFalse(inspect(b,'f'*32)['match'])
  self.assertEqual(inspect(b,RID)['boot_identity'],'unverified');self.assertEqual(inspect(b,RID)['match_scope'],'image-id-only')
  b[20]^=1;self.assertEqual(inspect(b)['classification'],'torn-or-invalid-header')
  b=identify(make());b[40]=ord('z');self.assertEqual(inspect(b)['classification'],'invalid-identity')
  b=identify(make());b[BLOCK]^=1
  self.assertEqual(inspect(b,RID)['classification'],'identified-header')
  self.assertEqual(inspect(b,RID)['payload_integrity'],'unverified')
  b=identify_v2(make(),3,600);v=inspect(b,RID)
  self.assertEqual(v['classification'],'identified-header');self.assertEqual(v['dropped_chunks'],3);self.assertEqual(v['dropped_bytes'],600)
  self.assertIn('independent-samples',v['drop_count_scope']);self.assertEqual(v['payload_integrity'],'unverified')
  b[72]^=1;self.assertEqual(inspect(b,RID)['classification'],'torn-or-invalid-header')
 def test_decoder_does_not_replace_output_or_claim_bad_header(self):
  with tempfile.TemporaryDirectory(prefix='sweetlog-decode-') as t:
   p=Path(t);raw=p/'raw.bin';out=p/'out.txt';raw.write_bytes(identify(make()));out.write_text('retained')
   r=subprocess.run(['python3','-I',str(SCRIPT),'--expect-run-id',RID,str(raw),str(out)],capture_output=True)
   self.assertNotEqual(r.returncode,0);self.assertEqual(out.read_text(),'retained')
   out.unlink();b=identify(make());b[72]^=1;raw.write_bytes(b)
   r=subprocess.run(['python3','-I',str(SCRIPT),str(raw),str(out)],capture_output=True)
   self.assertNotEqual(r.returncode,0);self.assertFalse(out.exists());self.assertEqual(raw.read_bytes(),b)
   raw.write_bytes(make());r=subprocess.run(['python3','-I',str(SCRIPT),'--expect-run-id',RID,str(raw),str(out)],capture_output=True)
   self.assertNotEqual(r.returncode,0);self.assertFalse(out.exists())
   raw.write_bytes(identify(make()));r=subprocess.run(['python3','-I',str(SCRIPT),'--expect-run-id',RID,str(raw),str(out)],capture_output=True)
   self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(out.read_text(),'test')
if __name__=='__main__':unittest.main(verbosity=2)
