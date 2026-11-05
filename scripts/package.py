#!/usr/bin/env python3
"""Create a source release; omit local profiles, caches and review artifacts."""
import hashlib
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
out = root / 'dist'
out.mkdir(exist_ok=True)
version = '1.0.0'
archive_path = out / f'ryujin-lcd-studio-{version}.tar.gz'
include = ('app.py', 'backend.py', 'sensors.py', 'README.md', 'LICENSE',
           'requirements.txt', 'setup.sh', 'install.sh', 'tests', 'docs', 'examples', 'scripts', 'udev')
with tarfile.open(archive_path, 'w:gz') as archive:
    for name in include:
        archive.add(root / name, arcname=f'ryujin-lcd-studio-{version}/{name}',
                    filter=lambda entry: None if '__pycache__' in entry.name or entry.name.endswith('.pyc') else entry)
(out / 'SHA256SUMS').write_text(f'{hashlib.sha256(archive_path.read_bytes()).hexdigest()}  {archive_path.name}\n')
print(archive_path.name)
