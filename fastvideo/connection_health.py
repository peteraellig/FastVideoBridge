"""Recent receiver health, with startup grace and recovery hysteresis."""
import time

class ConnectionHealth:
    def __init__(self):
        self.reset()
    def reset(self):
        self.previous=None;self.sample_at=0;self.slow=0;self.until=0;self.message='';self.signature=None;self.grace_until=0
    def update(self, data, now=None):
        now=time.monotonic() if now is None else now
        signature=(data.get('session'),str(data.get('video_format')),round(data.get('video_rate') or 0,2))
        sdi=data.get('sdi') or {};completion=sdi.get('completion') or {}
        counters=(data.get('video_queue_drops',0),data.get('audio_queue_drops',0),sdi.get('video_drops',0),sdi.get('audio_drops',0),completion.get('late',0),completion.get('dropped',0))
        if signature!=self.signature or self.previous and any(a<b for a,b in zip(counters,self.previous)):
            self.reset();self.signature=signature;self.previous=counters;self.sample_at=now;self.grace_until=now+3
        if not data.get('enabled') or data.get('state') not in ('receiving','connected','sending'):
            self.reset();return {'warning':False,'warning_text':''}
        if now-self.sample_at>=1:
            reasons=[]
            if now>=self.grace_until and self.previous:
                labels=('Video receive queue drops','Audio receive queue drops','SDI video queue drops','SDI audio drops','SDI late frames','SDI dropped frames')
                reasons.extend(f'{label}: +{new-old}' for label,new,old in zip(labels,counters,self.previous) if new>old)
            budget=1000/max(1,data.get('video_rate') or 50)
            stages=data.get('output_timings',{})
            dec=data.get('decoder') or {}
            decode=(dec.get('timings') or {}).get('decode_including_gpu_download',{})
            decode_ms=decode.get('p95_ms',0)
            output_ms=stages.get('video_output_including_thread_wait',{}).get('p95_ms',0)
            slow=decode_ms>budget or output_ms>budget
            self.slow=self.slow+1 if slow else 0
            if self.slow>=3:
                stage='Decode' if decode_ms>budget else 'Output'
                measured=decode_ms if decode_ms>budget else output_ms
                reasons.append(f'{stage} p95 {measured:.1f} ms > {budget:.1f} ms frame budget')
            if data.get('video_age_ms',0)>3000:reasons.append('Receiver is not getting video')
            if data.get('audio_age_ms',0)>3000:reasons.append('Receiver audio is interrupted')
            if reasons:self.message=' · '.join(reasons);self.until=now+6
            self.previous=counters;self.sample_at=now
        active=now<self.until
        return {'warning':active,'warning_text':self.message if active else ''}


class OutputSettling:
    """After a connection starts, the output settles: SDI lead trimming drops a few pictures on
    purpose and the first pictures can overflow a queue. Loss counters are reported from the
    moment it has settled, so 0 means "nothing lost since synchronised". Settled = no counter
    changed for QUIET seconds (at least MINIMUM after the start); after LIMIT seconds the counters
    start anyway, so a lasting problem is never hidden."""
    QUIET, MINIMUM, LIMIT = 3.0, 2.0, 20.0

    def __init__(self):
        self.session = None

    @staticmethod
    def _counters(data):
        sdi = data.get('sdi') or {}
        completion = sdi.get('completion') or {}
        return {'video_queue_drops': data.get('video_queue_drops') or 0, 'audio_queue_drops': data.get('audio_queue_drops') or 0,
                'queue_drops': data.get('queue_drops') or 0, 'sdi_video': sdi.get('video_drops') or 0,
                'sdi_audio': sdi.get('audio_drops') or 0, 'late': completion.get('late') or 0, 'dropped': completion.get('dropped') or 0,
                # Not shown as losses, but the output is not settled while it still trims or re-anchors.
                'trims': sdi.get('lead_trims') or 0, 'resyncs': sdi.get('resyncs') or 0}

    def apply(self, data, now=None):
        """Rebase the counters in data; returns True at the moment the output has settled."""
        now = time.monotonic() if now is None else now
        session = data.get('session') if data.get('enabled') and data.get('state') in ('connected', 'receiving') else None
        data['settling'] = False
        if session is None:
            self.session = None
            return False
        raw, settled_now = self._counters(data), False
        if session != self.session:
            self.session, self.started, self.quiet_since, self.last, self.baseline = session, now, now, raw, None
        if self.baseline is None:
            if raw != self.last:
                self.quiet_since, self.last = now, raw
            if now - self.quiet_since >= self.QUIET and now - self.started >= self.MINIMUM or now - self.started >= self.LIMIT:
                self.baseline, settled_now = dict(raw), True
        elif any(raw[k] < self.baseline[k] for k in raw):
            self.baseline = dict(raw)  # counters restarted (output re-created)
        base = self.baseline or raw
        delta = {k: raw[k] - base[k] for k in raw}
        data.update(video_queue_drops=delta['video_queue_drops'], audio_queue_drops=delta['audio_queue_drops'], queue_drops=delta['queue_drops'])
        if data.get('sdi'):
            sdi = dict(data['sdi'])
            sdi.update(video_drops=delta['sdi_video'], audio_drops=delta['sdi_audio'],
                       completion=dict(sdi.get('completion') or {}, late=delta['late'], dropped=delta['dropped']))
            data['sdi'] = sdi
        if self.baseline is None:
            data.update(settling=True, warning=False, warning_text='')
        return settled_now
