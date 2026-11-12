"""Local supervisor. Only children started here are restarted or terminated."""
import json
import logging
from logging.handlers import RotatingFileHandler
import msvcrt
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = ROOT / 'work'

def housekeeping(work, now=None, keep_days=7, limit=5_000_000):
    """Before any child starts: remove OMT logs of earlier processes (one file per process id,
    never deleted before) and start a new file for the logs that are only ever appended to."""
    now = time.time() if now is None else now
    for file in work.glob('omt-*.log'):
        try:
            if now-file.stat().st_mtime > keep_days*86400: file.unlink()
        except OSError: pass
    for name in ('server-supervised.log','gateway-supervised.log','isolated-encoder.err','receive-media.err','receiver-process.err'):
        file = work/name
        try:
            if file.exists() and file.stat().st_size > limit: file.replace(work/(name+'.1'))
        except OSError: pass

def run():
    WORK.mkdir(exist_ok=True)
    lock = (WORK / 'service.lock').open('a+b')
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        return
    housekeeping(WORK)
    logging.basicConfig(level=logging.INFO,handlers=[RotatingFileHandler(WORK/'service.log',maxBytes=1_000_000,backupCount=2)],format='%(asctime)s %(message)s')
    import os
    from fastvideo.process_priority import opt_out
    opt_out()
    (WORK/'pid-service.txt').write_text(str(os.getpid()),encoding='ascii')
    settings = json.loads((ROOT/'fastvideo.settings.json').read_text(encoding='utf-8-sig'))
    stop = WORK/'stop-request'
    children = {}
    attempts = {}
    streams = []
    http_port=settings.get('http_port',8879)
    gateway_port=settings.get('gateway_port',8880)
    bridge_port=settings.get('bridge_port',20999)
    from fastvideo.startup_ports import select_pair
    selected=select_pair(bridge_port,(http_port,gateway_port))
    if selected!=bridge_port:
        logging.info('Internal media ports changed from %s/%s to %s/%s',bridge_port,bridge_port+1,selected,selected+1)
        settings['bridge_port']=selected
        config_path=ROOT/'fastvideo.settings.json'
        temporary=config_path.with_suffix('.startup.tmp')
        temporary.write_text(json.dumps(settings,indent=2),encoding='utf-8')
        temporary.replace(config_path)
        bridge_port=selected
    ports=(http_port,gateway_port,bridge_port)
    if any(type(port) is not int or not 1024<=port<=65535 for port in ports) or len(set(ports)) != 3:
        raise ValueError('Ungültige Portkonfiguration')
    commands = {'gateway':[str(ROOT/'gateway'/'fastvideo-gateway.exe'),'--max-viewers',str(settings['max_viewers']),
                          '--port',str(gateway_port),'--codec',settings.get('codec','h264'),'--bridge-port',str(bridge_port),'--app-port',str(http_port),'--stun',','.join(settings.get('stun_urls',[])),
                          '--public-ipv4',settings.get('public_ipv4','')],
                'server':[sys.executable,str(ROOT/'scripts'/'app.py'),'--gateway','--port',str(http_port),
                          '--gateway-port',str(gateway_port),'--bridge-port',str(bridge_port)]}
    try:
        while not stop.exists():
            for name, command in commands.items():
                process = children.get(name)
                if process is not None and process.poll() is None:
                    continue
                now = time.monotonic()
                if process is not None and now-attempts[name] < 10:
                    continue
                if process is not None:
                    logging.warning('%s exited (%s), restarting',name,process.returncode)
                out = (WORK/(name+'-supervised.log')).open('ab')
                streams.append(out)
                if name == 'gateway':
                    latest = json.loads((ROOT/'fastvideo.settings.json').read_text(encoding='utf-8-sig'))
                    command[command.index('--max-viewers')+1] = str(latest['max_viewers'])
                children[name] = subprocess.Popen(command,cwd=ROOT,stdout=out,stderr=out,creationflags=subprocess.CREATE_NO_WINDOW)
                attempts[name] = now
                # The Go gateway cannot opt itself out of power throttling.
                throttling_off = opt_out(children[name]._handle)
                logging.info('%s started PID %s (power throttling off: %s)',name,children[name].pid,throttling_off)
                if name == 'gateway':
                    (WORK/'pid-gateway.txt').write_text(str(children[name].pid),encoding='ascii')
                time.sleep(.5)
            time.sleep(1)
    finally:
        # Stop script requests this before shutting down the application's SDKs.
        for process in children.values():
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill()
        for stream in streams: stream.close()
        (WORK/'pid-service.txt').unlink(missing_ok=True)
        lock.close()

if __name__ == '__main__':
    run()
