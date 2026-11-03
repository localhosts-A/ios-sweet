#!/usr/bin/env python3
"""Exercise real UFS ADT transformation and rejection cases."""
import copy
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
import mkadt
spec = importlib.util.spec_from_file_location('transformer', here/'dali-ufs-two-lane-adt.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
mkadt.d22ap_product = lambda: None
raw = t.adt.serialize(mkadt.build(seed=bytes(64), dali=True))
out = t.transform(raw)
root, end = t.adt.parse(out)
assert end == len(out)
ufs = next(n for n in root.children if n.name == 'sweet-ufs')
assert t.one(ufs, t.PROPERTY) == struct.pack('<I',1)
ufs.props = [(k,f,v) for k,f,v in ufs.props if k != t.PROPERTY]
assert t.adt.serialize(root) == raw


def rejected(candidate):
    try:
        t.transform(candidate)
    except (ValueError, AssertionError, struct.error, IndexError):
        return
    raise AssertionError('invalid ADT accepted')

rejected(out)
rejected(raw+b'\0')
root, _ = t.adt.parse(raw)
ufs = next(n for n in root.children if n.name == 'sweet-ufs')
ufs.props.append((t.PROPERTY,0,b'\0'))
rejected(t.adt.serialize(root))
ufs.props.pop()
ufs.props.append(('name',0,b'sweet-ufs\0'))
rejected(t.adt.serialize(root))
ufs.props.pop()
ufs.props.append(('compatible',0,b'mediatek,mt6991-ufshc\0'))
rejected(t.adt.serialize(root))
ufs.props.pop()
root.children.append(copy.deepcopy(ufs))
rejected(t.adt.serialize(root))
root.children.pop()
ufs.props = [(k,f,b'qcom,ufshc\0' if k=='compatible' else v) for k,f,v in ufs.props]
rejected(t.adt.serialize(root))
root, _ = t.adt.parse(raw)
root.props = [(k,f,b'xiaomi,sweet\0' if k=='compatible' else v) for k,f,v in root.props]
rejected(t.adt.serialize(root))

with tempfile.TemporaryDirectory() as temp:
    p=Path(temp); src=p/'in.adt'; dst=p/'out.adt'
    src.write_bytes(raw)
    cmd=[sys.executable,'-I',str(here/'dali-ufs-two-lane-adt.py'),str(src),str(dst)]
    subprocess.run(cmd,check=True,capture_output=True,text=True)
    assert dst.read_bytes()==out and src.read_bytes()==raw
    again=subprocess.run(cmd,capture_output=True,text=True)
    assert again.returncode != 0 and dst.read_bytes()==out and src.read_bytes()==raw
print('PASS actual ADT transformer: byte-preservation and malformed/duplicate rejections')
