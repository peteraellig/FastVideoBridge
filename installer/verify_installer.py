"""Install the actual EXE into an isolated folder, verify every payload file,
then exercise its own embedded runtime and supervisor. Preserve registration
of an existing per-user Bridge installation. Never start production outputs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
import winreg

parser = argparse.ArgumentParser()
parser.add_argument('--setup', type=Path, required=True)
parser.add_argument('--report', type=Path, required=True)
args = parser.parse_args()
setup = args.setup.resolve(strict=True)
root = Path(tempfile.mkdtemp(prefix='FastVideoBridge-installer-test-'))
installation = root / 'FastVideoBridge'
registry = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{05AE6B2A-1031-4DBE-9251-52507DE5E804}_is1'
backup = root / 'existing-registration.reg'
report = {'installer': str(setup), 'test_folder': str(root), 'passed': False}
supervisor = None
installed = False
registration_existed = False

def command(argv, timeout=180):
    return subprocess.run([str(a) for a in argv], check=True, timeout=timeout,
                          capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW)

def get_json(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)

def unused_ports():
    held = []
    try:
        for _ in range(3):
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            held.append(sock)
        return [sock.getsockname()[1] for sock in held]
    finally:
        for sock in held:
            sock.close()

try:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry):
            registration_existed = True
    except FileNotFoundError:
        pass
    if registration_existed:
        command(['reg.exe', 'export', 'HKCU\\' + registry, backup, '/y'])
    command([setup, '/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES',
             '/NORESTART', '/NOICONS', '/TASKS=', '/DIR=' + str(installation),
             '/LOG=' + str(root / 'install.log')])
    installed = True
    manifest_file = next(path for path in (installation / 'documents/PAYLOAD-MANIFEST.json', installation / '.runtime/PAYLOAD-MANIFEST.json') if path.is_file())
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    report['text_files'] = sorted(path.relative_to(installation).as_posix() for path in installation.rglob('*')
        if path.is_file() and '.runtime' not in path.parts and path.suffix.lower() in ('.md', '.txt', '.pdf', '.rtf', '.h'))
    missing, mismatched = [], []
    for row in manifest:
        path = installation / row['path']
        if not path.is_file():
            missing.append(row['path'])
        elif hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            mismatched.append(row['path'])
    report.update(payload_files=len(manifest), missing=missing, mismatched=mismatched)
    if missing or mismatched:
        raise RuntimeError(f'Installed payload incomplete: {len(missing)} missing, {len(mismatched)} hash mismatches')
    python = installation / '.runtime/python.exe'
    imports = command([python, '-I', '-c',
        'import aiohttp, aiortc, av, cv2, numpy, cryptography; '
        'import fastvideo.whip_receive, fastvideo.whep_receive, fastvideo.ndi_output, fastvideo.vmx_output, fastvideo.sdi_input, fastvideo.sdi_output; '
        'import pathlib,sys; root=pathlib.Path(sys.executable).resolve().parents[1]; '
        'assert all(pathlib.Path(m.__file__).resolve().is_relative_to(root) '
        'for m in [aiohttp,aiortc,av,cv2,numpy,cryptography,fastvideo.whip_receive]); '
        'encoder=fastvideo.vmx_output.VmxOutput(640,360,601); '
        'pointer,length=encoder.packet(av.VideoFrame(640,360,"nv12")); '
        'assert pointer and length>0; encoder.close(); '
        'print("Installed runtime imports and VMX encoding OK")'])
    report['imports'] = imports.stdout.strip()
    http_port, gateway_port, bridge_port = unused_ports()
    settings_path = installation / 'fastvideo.settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    settings.update(http_port=http_port, gateway_port=gateway_port, bridge_port=bridge_port,
                    public_base_url='', public_ipv4='', stun_urls=[])
    temporary_settings = settings_path.with_suffix('.smoke.tmp')
    temporary_settings.write_text(json.dumps(settings, indent=2), encoding='utf-8')
    temporary_settings.replace(settings_path)
    output = (root / 'startup.log').open('wb')
    try:
        supervisor = subprocess.Popen([str(python), str(installation / 'fastvideo/service.py')],
            cwd=installation, stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if supervisor.poll() is not None:
                raise RuntimeError('Installed supervisor exited before HTTP startup')
            try:
                dashboard=get_json(f'http://127.0.0.1:{http_port}/api/dashboard')
                if not all(k in dashboard for k in ('status','whip','srt','system','receive')):raise ValueError('Incomplete dashboard sample')
                get_json(f'http://127.0.0.1:{http_port}/api/status')
                get_json(f'http://127.0.0.1:{gateway_port}/whip-output')
                break
            except (OSError, ValueError):
                time.sleep(.5)
        else:
            raise RuntimeError('Installed server/gateway did not become reachable')
        with urllib.request.urlopen(f'http://127.0.0.1:{http_port}/sender', timeout=3) as response:
            page = response.read().decode('utf-8')
        if 'receiveSdiDevice' not in page or 'value="sdi"' not in page:raise RuntimeError('SDI output selector missing from installed UI')
        sdi_status=get_json(f'http://127.0.0.1:{http_port}/api/sdi/devices')
        if 'devices' not in sdi_status:raise RuntimeError('SDI device discovery API missing')
        report['sdi_output_selector']=True
        if 'receiveOutputKind' not in page:
            raise RuntimeError('NDI output selection missing from installed UI')
        report.update(server_http=200, gateway_http=200, dashboard_http=200, ndi_output_selector=True)
        time.sleep(3)
        get_json(f'http://127.0.0.1:{http_port}/api/status')
        service_log = (installation / 'work/service.log').read_text()
        if 'restarting' in service_log:
            raise RuntimeError('Installed child process restarted during startup test')
        report['passed'] = True
    finally:
        if supervisor:
            (installation / 'work/stop-request').touch()
            try:
                supervisor.wait(timeout=12)
            except subprocess.TimeoutExpired:
                supervisor.terminate()
                supervisor.wait(timeout=5)
        # SDK/encoder children may outlive a forcibly terminated supervisor.
        # Match the unique test directory, never another Bridge installation.
        cleanup = root / 'stop-test-children.ps1'
        cleanup.write_text("""param([string]$TestRoot)
$testPattern = [regex]::Escape($TestRoot)
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='fastvideo-gateway.exe'" |
    Where-Object { $_.CommandLine -match $testPattern } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
""", encoding='utf-8')
        command(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                 '-File', cleanup, '-TestRoot', installation])
        output.close()
except Exception as exc:
    report['error'] = str(exc)
finally:
    cleanup_errors = []
    try:
        uninstaller = installation / 'scripts/unins000.exe'
        if uninstaller.exists():
            command([uninstaller, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                     '/LOG=' + str(root / 'uninstall.log')])
    except Exception as exc:
        cleanup_errors.append(str(exc))
    try:
        if registration_existed:
            command(['reg.exe', 'import', backup])
        report['registration_restored'] = True
    except Exception as exc:
        cleanup_errors.append(str(exc))
    if cleanup_errors:
        report['passed'] = False
        report['cleanup_error'] = cleanup_errors
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report), flush=True)
raise SystemExit(0 if report['passed'] else 1)
