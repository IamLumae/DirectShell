"""Run inside the isolated, pinned build-attia/venv on Windows."""
from importlib import metadata
from pathlib import Path
import subprocess
import sys
import hashlib
import json
import PyInstaller.__main__

root = Path(__file__).resolve().parents[1]
source_files = [root/'Cargo.toml',root/'Cargo.lock',root/'build.rs',root/'ds-mcp/GUIDE.md',
                *sorted((root/'src').rglob('*.rs')),
                *(p for p in sorted((root/'ds-mcp').glob('*.py')) if not p.name.startswith('test_') and p.name!='cdp_test.py')]
def source_hashes():
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
sources_before=source_hashes()
subprocess.run(['cargo', 'build', '--locked', '--release', '--target-dir', str(root / 'build-attia/rust')], cwd=root, check=True)
args = ['--noconfirm', '--onedir', '--name', 'attia-ds-mcp',
        '--distpath', str(root / 'build-attia/dist'), '--workpath', str(root / 'build-attia/pyinstaller'),
        '--specpath', str(root / 'build-attia'), '--paths', str(root / 'ds-mcp'),
        '--add-data', str(root / 'ds-mcp/GUIDE.md') + ';.',
        '--collect-all', 'fastmcp', '--collect-all', 'key_value', '--collect-all', 'caio',
        '--collect-data', 'mcp', '--hidden-import', 'websocket',
        '--hidden-import', 'win32job', '--hidden-import', 'win32api']
# Optional dependency extras use metadata at runtime; include the exact isolated
# environment's metadata, not the workstation's global site-packages.
if Path(sys.prefix).resolve() != (root / 'build-attia/venv').resolve():
    raise RuntimeError('Use the isolated ATTIA build venv.')
for distribution in metadata.distributions():
    args += ['--copy-metadata', distribution.metadata['Name']]
args.append(str(root / 'ds-mcp/attia_entry.py'))
PyInstaller.__main__.run(args)
if source_hashes()!=sources_before:
    raise RuntimeError('Sources changed during build; do not stage these artifacts.')
artifacts={relative:hashlib.sha256((root/relative).read_bytes()).hexdigest() for relative in (
    'build-attia/rust/release/directshell.exe','build-attia/dist/attia-ds-mcp/attia-ds-mcp.exe')}
(root/'build-attia/dist/attia-ds-mcp/build-provenance.json').write_text(
    json.dumps({'version':1,'sources':sources_before,'artifacts':artifacts},indent=2)+'\n',encoding='utf-8')
