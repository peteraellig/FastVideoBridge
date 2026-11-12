"""Bounded, persistent warning history, independent of open control pages."""
from collections import OrderedDict
from datetime import datetime, timezone
import json
from logging.handlers import RotatingFileHandler
import logging
import time
import threading
from functools import wraps

def locked(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self.lock:return method(self,*args,**kwargs)
    return call


class WarningHistory:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock=threading.RLock()
        self.records=OrderedDict();self.active={};self.previous=None;self.text=''
        self.handler=RotatingFileHandler(path,maxBytes=1_000_000,backupCount=2,encoding='utf-8')
        self.handler.setFormatter(logging.Formatter('%(message)s'))
        for file in [path.with_name(path.name+'.2'),path.with_name(path.name+'.1'),path]:
            if not file.exists():continue
            for line in file.read_text(encoding='utf-8').splitlines():
                try:
                    record=json.loads(line);self.records[record['id']]=record
                except (ValueError,KeyError):continue
        self._trim()
        # An earlier process cannot still own an active warning.
        for record in list(self.records.values()):
            if record.get('ended_at') is None:
                record['ended_at']=self.utc(time.time());record['status']='Interrupted'
                self._write(record)

    @staticmethod
    def utc(at):return datetime.fromtimestamp(at,timezone.utc).isoformat()

    def _trim(self):
        while len(self.records)>200:
            key=next((key for key in self.records if key not in {r['id'] for r in self.active.values()}),None)
            if key is None:break
            del self.records[key]

    def _write(self,record):
        event=logging.LogRecord('fastvideo.warnings',logging.WARNING,'',0,json.dumps(record),(),None)
        self.handler.emit(event)

    @locked
    def update(self, sample, at=None):
        at=time.time() if at is None else at
        warnings=OrderedDict()
        if sample.get('error'):
            warnings['source']='Audio/video source timestamps do not match' if 'Quellzeitcodes' in sample['error'] else 'Source error · check the input'
        for field,code,message in [('gateway_error','gateway','WebRTC service unavailable'),('gateway_bridge_error','bridge','Stream transport interrupted'),('publisher_error','encoder','Encoder error · check the server log')]:
            if sample.get(field):warnings[code]=message
        if sample.get('running') and not sample.get('signal'):
            name=(sample.get('source') or {}).get('name')
            warnings['signal']='No video signal'+(' · '+name if name else '')
        if sample.get('running') and sample.get('signal'):
            for value,name,code in [(sample,(sample.get('video_codec') or 'AV1').upper(),'main-budget'),(sample.get('h264_fallback'),'H.264','h264-budget')]:
                if not value:continue
                count=value.get('encode_sample_count',0);p95=value.get('encode_ms_p95');budget=value.get('encode_budget_ms')
                if count and isinstance(p95,(int,float)) and isinstance(budget,(int,float)) and (p95>budget or value.get('encode_over_budget_recent',0)/count>=.05):
                    warnings[code]=f'{name} encoding over budget · P95 {p95:.1f} / {budget:.1f} ms'
            previous=self.previous
            if previous and sample.get('uptime_seconds',0)>=previous.get('uptime_seconds',0) and sample.get('frames',0)>=previous.get('frames',0):
                video=sample.get('frame_deadline_drops',0)-previous.get('frame_deadline_drops',0)
                if video>0:warnings['video-drop']=f'{video} input video frame'+('s' if video!=1 else '')+' skipped'
                missing=sample.get('audio_missing_samples',0)-previous.get('audio_missing_samples',0)
                dropped=sample.get('audio_buffer_drops',0)-previous.get('audio_buffer_drops',0)
                audio_reasons=[]
                if missing>0:audio_reasons.append(f'Audio input missing {missing/48:.1f} ms since last check')
                if dropped>0:audio_reasons.append(f'Audio input buffer overrun: {dropped} packet'+('s' if dropped!=1 else ''))
                if audio_reasons:warnings['audio-drop']=' · '.join(audio_reasons)
        self.previous={key:sample.get(key,0) for key in ['uptime_seconds','frames','frame_deadline_drops','audio_buffer_drops','audio_missing_samples']}
        for code in list(self.active):
            if code not in warnings:
                record=self.active.pop(code);record['ended_at']=self.utc(at);record['status']='Resolved';self._write(record)
        for code,message in warnings.items():
            if code in self.active:
                self.active[code]['message']=message
            else:
                key=f'{at:.6f}-{code}'
                record={'id':key,'code':code,'message':message,'started_at':self.utc(at),'ended_at':None,'status':'Active'}
                self.records[key]=record;self.active[code]=record;self._write(record)
        self.text=' · '.join(warnings.values());self._trim()

    @locked
    def snapshot(self):
        now=time.time();rows=[]
        for record in reversed(self.records.values()):
            row=dict(record)
            start=datetime.fromisoformat(row['started_at']).timestamp()
            end=datetime.fromisoformat(row['ended_at']).timestamp() if row['ended_at'] else now
            row['duration_seconds']=None if row['status']=='Interrupted' else round(max(0,end-start),1);rows.append(row)
        return rows

    @locked
    def control_outage(self,start,end):
        key=f'{start:.6f}-control-status'
        record={'id':key,'code':'control-status','message':'Sender unreachable · status update failed',
                'started_at':self.utc(start),'ended_at':self.utc(end),'status':'Resolved'}
        self.records[key]=record;self._write(record);self._trim()

    @locked
    def close(self):
        for record in self.active.values():
            record['ended_at']=self.utc(time.time());record['status']='Stopped';self._write(record)
        self.active.clear();self.handler.close()
