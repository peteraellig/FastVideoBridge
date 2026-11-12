"""Create a clean offline payload; never include user data, work logs or tests."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import zipfile
import subprocess

ROOT=Path(__file__).resolve().parents[1]
VERSION=re.search(r"VERSION = '([^']+)'",(ROOT/'fastvideo'/'__init__.py').read_text(encoding='utf-8')).group(1)
parser=argparse.ArgumentParser()
parser.add_argument('--packages',type=Path,default=Path(r'C:\Fastvideo\.venv\Lib\site-packages'))
parser.add_argument('--stage',type=Path,default=ROOT/'installer'/'stage')
# Lean: only what the program needs to run. No documents, no notes, no update tool; third-party
# licence texts stay, because passing on those components requires them.
parser.add_argument('--lean',action='store_true')
args=parser.parse_args();stage=args.stage.resolve()
if stage.exists():raise SystemExit('Choose an empty staging directory; an existing stage is never overwritten.')
stage.mkdir(parents=True)
exclude=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo','dev-legacy')
for folder in ('fastvideo','static','vendor','scripts','images'):
    shutil.copytree(ROOT/folder,stage/folder,ignore=exclude)
(stage/'gateway').mkdir()
shutil.copy2(ROOT/'gateway/fastvideo-gateway.exe',stage/'gateway/fastvideo-gateway.exe')
for name in ('Start-Bridge.bat','Stop-Bridge.bat'):
    shutil.copy2(ROOT/name,stage/name)
if args.lean:
    for extra in ('vendor/omt/README.txt','vendor/omt/libomt.h','static/connection-guide.html','static/connection-guide.css',
                  'images/Thumbs.db','images/fastvideobridge.png','scripts/Update-Bridge.ps1','scripts/Update-Bridge.vbs'):
        (stage/extra).unlink(missing_ok=True)
else:
    (stage/'documents').mkdir()
    for name in ('BRIDGE-README.md','requirements.lock.txt','requirements.txt','NDI-OUTPUT.txt'):
        shutil.copy2(ROOT/'documents'/name,stage/'documents'/name)
# No source discovery history, selected source, passwords, tokens, user lists or logs.
settings=json.loads((ROOT/'fastvideo.settings.json').read_text(encoding='utf-8'))
settings.update(codec='h264',encoder='auto',bridge_port=20999,timestamp_marker=False,viewer_title='FastVideo Bridge',public_base_url='',public_ipv4='',stun_urls=[],
                # Measured low-latency defaults: NVENC p7 took ~30 ms per 1080p50 frame, p4 ~4 ms.
                nvenc_preset='p4',qsv_preset='veryfast',bitrate=4_000_000)
(stage/'fastvideo.settings.json').write_text(json.dumps(settings,indent=2),encoding='utf-8')
runtime=stage/'.runtime';runtime.mkdir()
with zipfile.ZipFile(ROOT/'installer/tools/python-3.12.10-embed-amd64.zip') as archive:archive.extractall(runtime)
(runtime/'python312._pth').write_text('python312.zip\n.\n..\nLib/site-packages\nimport site\n',encoding='ascii')
packages=runtime/'Lib/site-packages';packages.mkdir(parents=True)
for item in args.packages.iterdir():
    if item.name in ('pip','__pycache__') or item.name.startswith('pip-') or item.suffix in ('.pyc','.pyo'):continue
    if item.is_dir():shutil.copytree(item,packages/item.name,ignore=exclude)
    else:shutil.copy2(item,packages/item.name)
INSTALLATION_TEXT=("""FastVideo Bridge """+VERSION+""" - Windows x64

FastVideo Bridge is a software encoder and decoder that sends and receives live video
with WHIP. Apart from OBS, WHIP encoders are hardly available anywhere. It is easy to
install; behind a router, a single port has to be opened on the receiving side.

Python and the Go media gateway are bundled. No Python, Go or FFmpeg installation is required.
OMT runtime is bundled.
NDI is not included. For NDI input or output download and install NDI Tools from
https://ndi.video/tools/ (they include the NDI Runtime the Bridge uses).
NDI(R) is a registered trademark of Vizrt NDI AB.
For direct Blackmagic SDI input/output install current Blackmagic Desktop Video.
Install current graphics drivers. H264 selects NVIDIA NVENC, Intel Quick Sync or CPU automatically.

Start: FastVideo Bridge shortcut. Stop: Stop FastVideo Bridge shortcut.
Local controls: http://127.0.0.1:18879/sender
No command window opens during normal operation.
Default codecs: WHIP AV1/H264/H265, SRT H264/H265. Audio defaults: 256 kbit/s stereo total.
WHIP receive: the sender connects to this PC. For this a port must be opened on the
receiving PC (default 56208, TCP + UDP). How to do it is described in the program under
Settings and Help; read it there after the installation.

No source choice or output credentials from the development machine are distributed.
Settings and user data are preserved on upgrades and uninstall.

Third-party licenses: .runtime/LICENSE.txt, .runtime/Lib/site-packages/*dist-info/
and vendor/omt/LICENSE.txt. Go dependency license texts are in licenses/go/.
""")
if not args.lean:(stage/'documents'/'INSTALLATION.txt').write_text(INSTALLATION_TEXT,encoding='utf-8')
licenses=stage/'licenses/go';licenses.mkdir(parents=True)
for original in (ROOT/'gateway/vendor').rglob('*'):
    if original.is_file() and (original.name.upper().startswith('LICENSE') or original.name.upper().startswith('COPYING') or original.name.upper()=='NOTICE'):
        target=licenses/original.relative_to(ROOT/'gateway/vendor');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,target)
# NDI Tools are not redistributable: users install them from ndi.video (see INSTALLATION.txt).
# Application icon (all sizes 16-256) comes from images/fastvideobridge.ico.
if not (stage/'images'/'fastvideobridge.ico').is_file():raise SystemExit('Missing images/fastvideobridge.ico')
# Copied installed dependencies can carry Hidden/System attributes.
# Inno Setup excludes hidden source files; normalize the staged payload.
import os
if os.name == 'nt':
    import ctypes
    for payload_path in stage.rglob('*'):
        attributes = ctypes.windll.kernel32.GetFileAttributesW(str(payload_path))
        if attributes == -1 or not ctypes.windll.kernel32.SetFileAttributesW(str(payload_path), attributes & ~6):
            raise OSError('Could not normalize staged attributes: ' + str(payload_path))
# Test the copied embedded interpreter, not the developer's environment.
subprocess.run([str(runtime/'python.exe'), '-I', '-B', '-c',
    'import aiohttp, aiortc, av, cv2, numpy, cryptography; '
    'import fastvideo.whip_receive, fastvideo.ndi_output; '
    'print("Staged runtime imports OK", flush=True)'], check=True, cwd=stage)
manifest=[]
for path in sorted(stage.rglob('*')):
    relative=path.relative_to(stage)
    if path.is_file() and '__pycache__' not in relative.parts and path.suffix not in ('.pyc','.pyo'):
        manifest.append(dict(path=relative.as_posix(),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),bytes=path.stat().st_size))
# The file list the installed copy is checked against; the lean payload has no documents folder.
(stage/('.runtime' if args.lean else 'documents')/'PAYLOAD-MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
assert not (stage/'user').exists() and not (stage/'work').exists()
print(json.dumps(dict(stage=str(stage),files=len(manifest),bytes=sum(p['bytes'] for p in manifest))))
