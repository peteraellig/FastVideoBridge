"""WHIP/WHEP receiver in its own process.

Network receive, decode and SDI/OMT/NDI output must not compete with the
sender's capture/encode threads for one interpreter lock. The parent keeps the
BridgeReceive control surface; the child owns the real receiver.
"""
import asyncio
import hmac
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout, ClientError

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)
RPC_METHODS = ('restore_settings', 'enable', 'enable_whep', 'stop', 'set_ndi_audio_minus20', 'set_output_mode', 'set_public_ip')
FORWARD_HEADERS = ('Location', 'ETag', 'Accept-Patch', 'Cache-Control', 'WWW-Authenticate')


class _PreviewProxy:
    def __init__(self, owner):
        self.owner = owner
        self.work_ms = 0
        self.frame_wall_ms = None

    async def get(self):
        owner = self.owner
        if owner.base is None:
            return None
        try:
            async with owner.client.get(owner.base + '/control/preview', headers=owner._headers(),
                                        timeout=ClientTimeout(total=3)) as response:
                if response.status != 200:
                    return None
                self.work_ms = float(response.headers.get('X-Preview-Work-Ms', 0))
                self.frame_wall_ms = float(response.headers.get('X-Frame-Wall-Ms', 0)) or None
                return await response.read()
        except (ClientError, asyncio.TimeoutError, OSError, ValueError):
            return None


class ReceiverProcess:
    STATUS_INTERVAL = .25

    def __init__(self, port, addresses):
        from .whep_receive import BridgeReceive
        from .auth_throttle import AuthThrottle
        self.throttle = AuthThrottle()
        self.port = port
        self.addresses = addresses
        self.lock = asyncio.Lock()
        # Holds configured settings and answers status while no child is reachable.
        self.shadow = BridgeReceive(port, addresses)
        self.settings = {}
        self._output_mode = 'follow'
        self.replay = None
        self.ndi_override = None
        self.process = None
        self.base = None
        self.token = None
        self.client = None
        self.monitor = None
        self.ready = asyncio.Event()
        self.child_status = None
        self.child_status_at = 0
        self.restarts = 0
        self.failure = ''
        self.closing = False
        self.tasks = set()
        self.preview = _PreviewProxy(self)

    def _headers(self):
        return {'X-Bridge-Control': self.token or ''}

    def _fields(self):
        if self.child_status is not None and time.monotonic() - self.child_status_at < 3:
            return self.child_status
        return self.shadow.status()

    @property
    def output_mode(self):
        return self._output_mode

    @output_mode.setter
    def output_mode(self, mode):
        self._output_mode = mode
        self.shadow.output_mode = mode
        self._schedule('set_output_mode', mode)

    def restore_settings(self, settings):
        if self._fields().get('enabled'):
            return
        self.settings = dict(settings)
        self.shadow.restore_settings(self.settings)
        self.child_status = None
        self._schedule('restore_settings', self.settings)

    def status(self):
        data = dict(self._fields())
        if data.get('mode') != 'whep':
            data['urls'] = [f"http://{ip}:{data.get('listen_port') or self.port}/api/{data.get('endpoint_name', 'whip-input')}"
                            for ip in self.addresses()]
        if self.base is None and (self.replay or self.failure):
            data['detail'] = data.get('detail') or self.failure or 'Receiver process is restarting'
        data['receiver_process'] = {'pid': self.process.pid if self.process else None, 'restarts': self.restarts}
        return data

    def _schedule(self, method, *args):
        if self.base is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        async def send():
            try:
                await self._rpc(method, *args)
            except Exception:
                log.exception('Receiver process control %s failed', method)
        task = loop.create_task(send())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _rpc(self, method, *args, **kwargs):
        if self.base is None:
            raise ValueError('Receiver process is not available. Try again in a few seconds.')
        try:
            async with self.client.post(self.base + '/control/rpc', headers=self._headers(),
                                        json={'method': method, 'args': list(args), 'kwargs': kwargs}) as response:
                if response.status != 200:
                    raise ValueError(f'Receiver process control failed (HTTP {response.status})')
                data = await response.json()
        except (ClientError, asyncio.TimeoutError, OSError):
            raise ValueError('Receiver process did not respond. Try again in a few seconds.') from None
        if data.get('status') is not None:
            self.child_status = data['status']
            self.child_status_at = time.monotonic()
        if not data.get('ok'):
            raise (ValueError if data.get('value_error') else RuntimeError)(data.get('error') or 'Receiver control failed')

    async def _wait_ready(self):
        try:
            await asyncio.wait_for(self.ready.wait(), 30)
        except asyncio.TimeoutError:
            raise ValueError('Receiver process is not available. Check work\\receiver-process.err.') from None

    async def enable(self, *args, **kwargs):
        await self._wait_ready()
        self.replay = None
        self.ndi_override = None
        await self._rpc('enable', *args, **kwargs)
        self.replay = ('enable', args, kwargs)
        self.failure = ''

    async def enable_whep(self, *args, **kwargs):
        await self._wait_ready()
        self.replay = None
        self.ndi_override = None
        await self._rpc('enable_whep', *args, **kwargs)
        self.replay = ('enable_whep', args, kwargs)
        self.failure = ''

    def accepts_key(self, authorization):
        """True for 'Bearer <key>' of the running WHIP receiver (lets a sender read diagnostics)."""
        if not self.replay or self.replay[0] != 'enable' or not self.replay[1]:
            return False
        key = self.replay[1][0]
        return bool(key) and hmac.compare_digest((authorization or '').encode(), ('Bearer ' + key).encode())

    async def set_public_ip(self, public_ip):
        await self._wait_ready()
        await self._rpc('set_public_ip', public_ip)
        self.shadow.public_ip = public_ip
        self.settings = {**getattr(self, 'settings', {}), 'public_ip': public_ip}
        self.child_status = None
        # A restarted receiver process replays its enable call: keep the new address there too.
        if self.replay and self.replay[0] == 'enable' and len(self.replay[1]) > 3:
            args = list(self.replay[1]); args[3] = public_ip
            self.replay = ('enable', args, self.replay[2])

    async def set_ndi_audio_minus20(self, value):
        if type(value) is not bool:
            raise ValueError('NDI audio attenuation must be true or false')
        await self._wait_ready()
        await self._rpc('set_ndi_audio_minus20', value)
        self.ndi_override = value

    async def stop(self):
        self.replay = None
        self.ndi_override = None
        self.failure = ''
        if self.base is not None:
            try:
                await self._rpc('stop')
            except ValueError:
                log.exception('Receiver process did not confirm stop')

    async def offer(self, request):
        return await self._forward(request)

    async def resource(self, request):
        return await self._forward(request)

    async def _forward(self, request):
        if self.base is None:
            raise web.HTTPServiceUnavailable(text='Receiver is starting. Try again in a few seconds.')
        # The receiver process only sees 127.0.0.1 for forwarded requests.
        self.throttle.check(request.remote)
        body = await request.read()
        headers = {key: request.headers[key] for key in ('Authorization', 'Content-Type', 'If-Match') if key in request.headers}
        headers['X-Bridge-Remote'] = request.remote or ''
        try:
            async with self.client.request(request.method, self.base + request.rel_url.path, data=body or None,
                                           headers=headers, allow_redirects=False) as response:
                payload = await response.read()
                if response.status == 401:
                    self.throttle.failed(request.remote)
                elif response.status < 400:
                    self.throttle.succeeded(request.remote)
                forwarded = {key: response.headers[key] for key in FORWARD_HEADERS if key in response.headers}
                return web.Response(status=response.status, body=payload, headers=forwarded,
                                    content_type=response.content_type if payload else None)
        except (ClientError, asyncio.TimeoutError, OSError):
            raise web.HTTPServiceUnavailable(text='Receiver process did not respond') from None

    async def start(self):
        if self.monitor is None:
            self.client = ClientSession(timeout=ClientTimeout(total=35))
            self.monitor = asyncio.create_task(self._supervise())

    async def close(self):
        self.closing = True
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
            self.monitor = None
        process, self.process = self.process, None
        self.base = None
        self.ready.clear()
        if process:
            # EOF on stdin lets the child stop its SDI/OMT output cleanly.
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                await asyncio.to_thread(process.wait, 8)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
        if self.client:
            await self.client.close()
            self.client = None

    async def _launch(self):
        self.token = secrets.token_hex(16)
        (ROOT / 'work').mkdir(exist_ok=True)
        with open(ROOT / 'work' / 'receiver-process.err', 'ab') as errors:
            process = subprocess.Popen([sys.executable, '-m', 'fastvideo.receiver_process', str(self.port)], cwd=ROOT,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            process.stdin.write((self.token + '\n').encode())
            process.stdin.flush()
            line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 60)
            if not line.startswith(b'PORT '):
                raise RuntimeError('Receiver process did not start')
            port = int(line[5:])
        except BaseException:
            process.kill()
            await asyncio.to_thread(process.wait)
            raise
        process.stdout.close()
        self.process = process
        self.base = f'http://127.0.0.1:{port}'
        if self.settings:
            await self._rpc('restore_settings', self.settings)
        await self._rpc('set_output_mode', self._output_mode)
        if self.replay:
            method, args, kwargs = self.replay
            try:
                await self._rpc(method, *args, **kwargs)
                if self.ndi_override is not None:
                    await self._rpc('set_ndi_audio_minus20', self.ndi_override)
                self.failure = ''
            except Exception as exc:
                self.replay = None
                self.failure = f'Receiver restarted but could not resume: {exc}'
                log.error(self.failure)
        self.ready.set()
        log.info('Receiver process %s ready on %s', process.pid, self.base)

    async def _supervise(self):
        reported = False
        while True:
            try:
                if self.process is None or self.process.poll() is not None:
                    if self.process is not None:
                        self.restarts += 1
                        log.error('Receiver process ended with exit code %s; restarting', self.process.returncode)
                        if self.replay:
                            self.failure = 'Receiver process stopped unexpectedly; restarting'
                    self.process = None
                    self.base = None
                    self.child_status = None
                    self.ready.clear()
                    await self._launch()
                async with self.client.get(self.base + '/control/status', headers=self._headers(),
                                           timeout=ClientTimeout(total=2)) as response:
                    if response.status == 200:
                        self.child_status = await response.json()
                        self.child_status_at = time.monotonic()
                reported = False
            except asyncio.CancelledError:
                raise
            except Exception:
                if not reported:
                    log.exception('Receiver process supervision failed')
                    reported = True
                await asyncio.sleep(2)
            await asyncio.sleep(self.STATUS_INTERVAL)


def _status_field(key):
    return property(lambda self: self._fields().get(key))


for _key in ('enabled', 'mode', 'name', 'output_kind', 'ndi_audio_minus20', 'sdi_device', 'sdi_interlaced', 'viewer_url',
             'listen_port', 'public_ip', 'endpoint_name'):
    setattr(ReceiverProcess, _key, _status_field(_key))


async def _child(port, token):
    from . import whip_receive
    whip_receive.MEDIA_PROCESS = True
    from .whep_receive import BridgeReceive
    from .preview import ReceiverPreview
    receiver = BridgeReceive(port, lambda: [])
    preview = ReceiverPreview(receiver)
    loop = asyncio.get_running_loop()
    parent_gone = asyncio.Event()

    @web.middleware
    async def guard(request, handler):
        if request.path.startswith('/control/') and not hmac.compare_digest(
                request.headers.get('X-Bridge-Control', '').encode(), token.encode()):
            raise web.HTTPForbidden()
        return await handler(request)

    async def rpc(request):
        data = await request.json()
        method, args, kwargs = data.get('method'), data.get('args', []), data.get('kwargs', {})
        if method not in RPC_METHODS or not isinstance(args, list) or not isinstance(kwargs, dict):
            raise web.HTTPBadRequest()
        try:
            if method == 'restore_settings':
                receiver.restore_settings(*args)
            elif method == 'set_output_mode':
                receiver.output_mode = args[0]
            else:
                await getattr(receiver, method)(*args, **kwargs)
        except ValueError as exc:
            return web.json_response({'ok': False, 'value_error': True, 'error': str(exc), 'status': receiver.status()})
        except Exception as exc:
            log.exception('Receiver control %s failed', method)
            return web.json_response({'ok': False, 'value_error': False, 'error': str(exc) or type(exc).__name__,
                                      'status': receiver.status()})
        return web.json_response({'ok': True, 'status': receiver.status()})

    async def status(request):
        return web.json_response(receiver.status())

    async def preview_image(request):
        jpeg = await preview.get()
        if jpeg is None:
            return web.Response(status=204)
        return web.Response(body=jpeg, content_type='image/jpeg', headers={'X-Preview-Work-Ms': f'{preview.work_ms:.3f}',
                                                                           'X-Frame-Wall-Ms': f'{preview.frame_wall_ms or 0:.1f}'})

    app = web.Application(middlewares=[guard], client_max_size=65536)
    app.router.add_post('/control/rpc', rpc)
    app.router.add_get('/control/status', status)
    app.router.add_get('/control/preview', preview_image)
    # Legacy reception on the Bridge's own HTTP port is forwarded here.
    app.router.add_post('/api/whip-input', receiver.offer)
    app.router.add_get('/api/whip-input', lambda request: web.Response(status=204, headers={'Accept-Patch': 'application/trickle-ice-sdpfrag'}))
    for method in ('GET', 'DELETE', 'PATCH'):
        app.router.add_route(method, '/api/whip-input/session/{sid}', receiver.resource)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    control_port = site._server.sockets[0].getsockname()[1]
    sys.stdout.write(f'PORT {control_port}\n')
    sys.stdout.flush()
    # Nothing else may write into the parent's unread stdout pipe.
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    sys.stdout = open(os.devnull, 'w')

    def watch_parent():
        try:
            while sys.stdin.readline():
                pass
        finally:
            loop.call_soon_threadsafe(parent_gone.set)
    threading.Thread(target=watch_parent, daemon=True, name='Receiver-parent-watch').start()

    await parent_gone.wait()
    try:
        await asyncio.wait_for(receiver.stop(), 6)
    except Exception:
        log.exception('Receiver shutdown failed')
    await runner.cleanup()


def main():
    from .process_priority import opt_out
    opt_out()
    port = int(sys.argv[1])
    token = sys.stdin.readline().strip()
    (ROOT / 'work').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s',
                        handlers=[RotatingFileHandler(ROOT / 'work' / f'receiver-{port}.log', maxBytes=5_000_000,
                                                      backupCount=2, encoding='utf-8')])
    asyncio.run(_child(port, token))


if __name__ == '__main__':
    main()
