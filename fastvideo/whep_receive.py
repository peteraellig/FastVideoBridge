"""Outbound WHEP playback using the Bridge's shared hardware decoder/output path."""
import asyncio
import logging
import json
import time
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, urljoin

from aiohttp import ClientSession, ClientTimeout, ClientError
from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer, RTCBundlePolicy, RTCSessionDescription, RTCRtpSender

from . import APP_NAME, VERSION
from .whip_receive import WhipReceive
from .output_workers import OutputWorkers


class WhepError(Exception):
    def __init__(self, message, retry=True):
        super().__init__(message)
        self.retry = retry


def origin(url):
    p = urlsplit(url)
    return p.scheme.lower(), p.hostname.lower(), p.port or (443 if p.scheme == 'https' else 80)


def viewer_target(value, codec='auto'):
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError('Enter a FastVideo viewer link or WHEP URL')
    value = value.strip()
    try:
        p = urlsplit(value)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username is not None or p.password is not None or p.fragment:
            raise ValueError()
        p.port
    except ValueError:
        raise ValueError('Enter a complete http:// or https:// viewer link without a fragment') from None
    if any(c in value for c in '\r\n\x00') or codec not in ('auto', 'h264', 'av1', 'h265'):
        raise ValueError('Invalid viewer link or video codec')
    path = p.path.rstrip('/')
    fastvideo = path in ('', '/viewer', '/viewer.html')
    if not fastvideo and not path.endswith('/whep'):
        raise ValueError('Use the FastVideo /viewer link or a /whep endpoint')
    query = dict(parse_qsl(p.query, keep_blank_values=True))
    preferred = query.get('codec', 'auto') if codec == 'auto' else codec
    if preferred not in ('auto', 'h264', 'av1', 'h265'):
        raise ValueError('Unsupported video codec in viewer link')
    access = query.pop('access', '') if fastvideo else ''
    query.pop('codec', None)
    # Viewer-only parameters must not become credentials for another endpoint.
    endpoint = urlunsplit((p.scheme, p.netloc, '/whep' if fastvideo else path, '' if fastvideo else urlencode(query), ''))
    return dict(viewer_url=value, endpoint=endpoint, base=urlunsplit((p.scheme, p.netloc, '', '', '')),
                fastvideo=fastvideo, access=access, codec=preferred)


class BridgeReceive(WhipReceive):
    def __init__(self, port, addresses):
        super().__init__(port, addresses)
        self.mode = 'whip'
        self.viewer_url = ''
        self.preferred_codec = 'auto'
        self.pull_task = None
        self.pull_state = 'stopped'
        self.pull_attempts = 0
        self.client_id = str(uuid.uuid4())

    def restore_settings(self, settings):
        super().restore_settings(settings)
        if not self.enabled:
            self.mode = settings.get('mode', 'whip') if settings.get('mode', 'whip') in ('whip', 'whep') else 'whip'
            self.viewer_url = settings.get('viewer_url', '')
            self.preferred_codec = settings.get('codec', 'auto')

    def status(self):
        data = super().status()
        data.update(mode=self.mode, viewer_url=self.viewer_url, preferred_codec=self.preferred_codec, attempts=self.pull_attempts)
        if self.mode == 'whep':
            data.update(urls=[], public_url='')
            if self.enabled and data['state'] not in ('connected', 'receiving'):
                data['state'] = self.pull_state
            if self.pull_state == 'error' and self.enabled:
                data.update(warning=True, warning_text='')
        return data

    async def _cancel_pull(self):
        task, self.pull_task = self.pull_task, None
        if task:
            self.enabled = False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def enable(self, *args, **kwargs):
        await self._cancel_pull()
        self.mode = 'whip'
        await super().enable(*args, **kwargs)

    async def stop(self):
        await self._cancel_pull()
        await super().stop()
        self.pull_state = 'stopped'

    async def _close_session(self):
        # Pull reception uses outbound ICE; never reserve the WHIP listener port.
        port = self.listen_port
        if self.mode == 'whep':
            self.listen_port = 0
        try:
            await super()._close_session()
        finally:
            self.listen_port = port

    async def enable_whep(self, url, name='FastVideo Bridge Receive', output_kind='omt', ndi_audio_minus20=False, codec='auto', token='',sdi_device=None,sdi_interlaced=False):
        target = viewer_target(url, codec)
        if output_kind not in ('omt', 'ndi','sdi') or type(ndi_audio_minus20) is not bool:
            raise ValueError('Choose OMT, NDI or SDI output')
        if output_kind=='sdi' and (type(sdi_device) is not int or sdi_device<0):raise ValueError('Choose a Blackmagic SDI output connector')
        if type(sdi_interlaced) is not bool:raise ValueError('SDI interlaced must be true or false')
        if not isinstance(name, str) or not 1 <= len(name.encode()) <= 200 or any(c in name for c in '\r\n\x00'):
            raise ValueError('Enter an OMT / NDI output name (1-200 bytes)')
        if not isinstance(token, str) or len(token) > 8192 or any(c in token for c in '\r\n\x00'):
            raise ValueError('Invalid bearer token')
        await self.stop()
        async with self.lock:
            self.mode = 'whep'
            self.viewer_url = target['viewer_url']
            self.preferred_codec = codec
            self.output_kind = output_kind
            self.sdi_device=sdi_device
            self.sdi_interlaced=sdi_interlaced
            self.ndi_audio_minus20 = ndi_audio_minus20
            self.name = name
            try:
                self.sender = await asyncio.to_thread(self.make_sender,name)
                if output_kind == 'ndi':
                    self.sender.audio_minus20 = ndi_audio_minus20
                self.output_workers = OutputWorkers()
            except Exception as error:
                await self._stop()
                if output_kind=='sdi':raise ValueError(str(error)) from None
                raise ValueError('Selected output runtime could not start. Check OMT or NDI installation.') from None
            self.enabled = True
            self.detail = ''
            self.pull_state = 'connecting'
            self.pull_attempts = 0
            self.pull_task = asyncio.create_task(self._pull(target, token))

    async def _request(self, client, method, url, headers=None, data=None):
        async with client.request(method, url, headers=headers, data=data, allow_redirects=False) as response:
            chunks = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                chunks.extend(chunk)
                if len(chunks) > 65536:
                    raise WhepError('Streaming server response is too large', False)
            body = bytes(chunks)
            if 300 <= response.status < 400:
                raise WhepError('Use the final HTTPS viewer link; the server redirected this request', False)
            return response.status, response.headers, body

    async def _pull(self, target, token):
        async with ClientSession(timeout=ClientTimeout(total=15), headers={'User-Agent': f'{APP_NAME}/{VERSION}'}) as client:
            while self.enabled:
                self.pull_attempts += 1
                self.pull_state = 'connecting'
                self.detail = ''
                resource = None
                headers = {'Content-Type': 'application/sdp', 'Accept': 'application/sdp'}
                if target['access']:
                    headers.update({'X-FastVideo-Access': target['access'], 'X-FastVideo-Client': self.client_id})
                if token:
                    headers['Authorization'] = 'Bearer ' + token
                fatal = False
                try:
                    resource = await self._session(client, target, headers)
                except asyncio.CancelledError:
                    raise
                except WhepError as exc:
                    self.detail = str(exc)
                    fatal = not exc.retry
                except (ClientError, asyncio.TimeoutError, OSError):
                    self.detail = 'Streaming server unreachable or connection timed out. Retrying.'
                except Exception:
                    # The loop retries every few seconds: log the reason at most once a minute.
                    if time.monotonic() - getattr(self, 'failure_logged', -60) >= 60:
                        self.failure_logged = time.monotonic()
                        logging.getLogger(__name__).exception('WHEP reception failed')
                    self.detail = 'WHEP negotiation or incoming media failed. Retrying.'
                finally:
                    connection_detail = self.detail
                    # _session records a created resource immediately, including
                    # when SDP/ICE setup subsequently fails or Stop is pressed.
                    resource = getattr(self, '_session_resource', None) or resource
                    self._session_resource = None
                    if resource:
                        try:
                            async with client.delete(resource, headers=headers, allow_redirects=False, timeout=ClientTimeout(total=3)) as response:
                                await response.content.read(4096)
                        except (ClientError, asyncio.TimeoutError, OSError):
                            pass
                    async with self.lock:
                        await self._close_session()
                    self.detail = connection_detail
                if fatal:
                    self.pull_state = 'error'
                    return
                if self.enabled:
                    self.pull_state = 'retrying'
                    await asyncio.sleep(min(15, 2 ** min(self.pull_attempts, 4)))

    async def _session(self, client, target, headers):
        ice_servers = []
        available = ['h264', 'av1', 'h265']
        if target['fastvideo']:
            code, _, body = await self._request(client, 'GET', target['base'] + '/api/status')
            if code != 200:
                raise WhepError('FastVideo status unavailable (HTTP %d)' % code, code not in (401, 403))
            status = json.loads(body)
            available = status.get('video_codecs') or [status.get('video_codec', 'h264')]
            code, _, body = await self._request(client, 'GET', target['base'] + '/api/ice')
            if code != 200:
                raise WhepError('FastVideo ICE configuration unavailable (HTTP %d)' % code)
            for item in json.loads(body).get('iceServers', []):
                ice_servers.append(RTCIceServer(urls=item['urls'], username=item.get('username'), credential=item.get('credential')))
        codec = target['codec']
        if codec == 'auto':
            # H264 is hardware-decodable on the widest range of Bridge PCs.
            codec = next((c for c in ('h264', 'av1', 'h265') if c in available), None)
        if codec not in available:
            raise WhepError('Selected codec is not offered by the streaming server', False)
        pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers, bundlePolicy=RTCBundlePolicy.MAX_BUNDLE))
        async with self.lock:
            self.pc = pc
            self.sid = uuid.uuid4().hex
            self.bind_peer(pc)
        video = pc.addTransceiver('video', direction='recvonly')
        pc.addTransceiver('audio', direction='recvonly')
        preferences = [c for c in RTCRtpSender.getCapabilities('video').codecs if c.mimeType.lower() == 'video/' + codec]
        if not preferences:
            raise WhepError('Selected codec is unavailable in the Bridge decoder', False)
        video.setCodecPreferences(preferences)
        await asyncio.wait_for(pc.setLocalDescription(await pc.createOffer()), 15)
        endpoint = target['endpoint']
        if target['fastvideo']:
            endpoint += '?codec=' + codec
        code, response_headers, body = await self._request(client, 'POST', endpoint, headers, pc.localDescription.sdp)
        if code != 201:
            personal_busy = code == 409 and b'personal link' in body.lower()
            if code in (401, 403):
                raise WhepError('Viewer link or token is invalid, disabled, or a personal link is required', False)
            if personal_busy:
                raise WhepError('This personal link is already in use. Ask the organiser for another connection.', False)
            if code == 410:
                raise WhepError('Disconnected by organiser. Connect again only when permitted.', False)
            raise WhepError('Streaming server rejected the connection (HTTP %d). Retrying.' % code)
        location = response_headers.get('Location')
        if not location:
            raise WhepError('WHEP server did not provide a session address', False)
        resource = urljoin(endpoint, location)
        if origin(resource) != origin(endpoint) or urlsplit(resource).username is not None:
            raise WhepError('WHEP session must remain on the streaming server', False)
        self._session_resource = resource
        if response_headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/sdp':
            raise WhepError('WHEP server did not return an SDP answer', False)
        await asyncio.wait_for(pc.setRemoteDescription(RTCSessionDescription(body.decode('utf-8'), 'answer')), 15)
        self.codec = codec.upper()
        started = time.monotonic()
        last_check = started
        while self.enabled and self.pc is pc:
            await asyncio.sleep(.25)
            if pc.connectionState in ('failed', 'closed'):
                raise WhepError('Media connection interrupted. Retrying.')
            if pc.connectionState == 'connected':
                self.pull_state = 'connected'
                if time.monotonic() - (self.last_video or started) > 10:
                    raise WhepError('Connected but no video is arriving. Retrying.')
            elif time.monotonic() - started > 15:
                raise WhepError('Media connection timed out. Check outgoing UDP permissions. Retrying.')
            if target['fastvideo'] and time.monotonic() - last_check >= 3:
                last_check = time.monotonic()
                code, _, body = await self._request(client, 'GET', resource.rstrip('/') + '/state')
                if code == 200 and json.loads(body).get('disconnected'):
                    raise WhepError('Disconnected by organiser. Connect again only when permitted.', False)
        return resource
