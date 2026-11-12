"""Optional authenticated Bridge receiver telemetry; no WHIP protocol changes."""
import asyncio
import json
import re
import time
from urllib.parse import urlsplit, urlunsplit
from aiohttp import ClientSession, ClientTimeout, ClientError

class ReceiverFeedback:
    def __init__(self, gateway):
        self.gateway=gateway;self.configure({})
    def configure(self, config):
        self.config=dict(config);self.data=None;self.received_at=0;self.supported=False
    def snapshot(self):
        fresh=bool(self.received_at and time.monotonic()-self.received_at<8)
        data=self.data if fresh else None
        return {'receiver_feedback_supported':self.supported,'receiver_feedback':data,
                'warning':bool(data and data.get('warning')),
                'warning_text':data.get('warning_text','') if data else ''}
    async def run(self):
        async with ClientSession(timeout=ClientTimeout(total=3)) as http:
            while True:
                config=self.config
                try:
                    endpoint=urlsplit(config.get('url',''))
                    # Only query the Bridge extension, not arbitrary third-party WHIP paths.
                    if re.fullmatch(r'/api/[A-Za-z0-9][A-Za-z0-9_-]{0,63}',endpoint.path.rstrip('/')):
                        async with http.get(self.gateway+'/whip-output') as response:
                            state=await response.json()
                        if state.get('enabled') and state.get('state') in ('connected','sending'):
                            url=urlunsplit((endpoint.scheme,endpoint.netloc,endpoint.path.rstrip('/')+'/status','',''))
                            async with http.get(url,headers={'Authorization':'Bearer '+config.get('token','')},allow_redirects=False) as response:
                                if response.status==200:
                                    raw=await response.content.read(65537)
                                    if len(raw)>65536:raise ValueError('Telemetry too large')
                                    data=json.loads(raw)
                                    if config is self.config and isinstance(data,dict) and 'video_frames' in data:
                                        # Keep only diagnostics, never receiver keys, addresses or arbitrary fields.
                                        self.data={k:data.get(k) for k in ('warning','warning_text','video_format','video_rate','fps','video_frames','audio_frames','video_queue_drops','audio_queue_drops','decoder','state')}
                                        self.received_at=time.monotonic();self.supported=True
                        else:self.data=None;self.received_at=0
                except (ClientError,OSError,ValueError,asyncio.TimeoutError):pass
                await asyncio.sleep(2)
