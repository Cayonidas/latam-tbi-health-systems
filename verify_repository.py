"""Verify distributed file bytes using only the Python standard library."""
from pathlib import Path
import hashlib
root=Path(__file__).resolve().parent
lines=(root/'PACKAGE_CHECKSUMS.sha256').read_text(encoding='utf-8').splitlines()
for line in lines:
    digest,name=line.split('  ',1)
    path=root/name
    assert path.is_file(),name
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest,name
print(f'Verified {len(lines)} distributed files. Raw-data reconstruction is a separate execution.')
