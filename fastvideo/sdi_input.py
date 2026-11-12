"""Blackmagic capture callbacks -> bounded source queue, embedded audio 1+2."""
from collections import deque
import ctypes as C
from fractions import Fraction
import threading
import time
import subprocess
import sys
from pathlib import Path
from multiprocessing.shared_memory import SharedMemory
import av
import cv2
import numpy as np
from .decklink import Com,Callback,P,L,select,modes,discover
from .diagnostics import Timings

def fourcc(text):return int.from_bytes(text.encode('ascii'),'big')
UPPER_FIELD_FIRST,LOWER_FIELD_FIRST=fourcc('uppr'),fourcc('lowr')
# Progressive ('prog'), progressive segmented frame ('psf ', e.g. 1080PsF25) and unknown (0) arrive
# as whole pictures. 25 pictures per second are sent twice by the engine to keep 720p50.


def field_picture(image,parity,out=None):
    """One field of an interlaced picture (rows of UYVY or BGRA bytes) as a full-height picture
    ("bob" deinterlacing). parity 0 = top field (even rows), 1 = bottom field. The field's own rows
    stay where they were captured; the missing rows are the average of their neighbours, so the
    picture does not jump up and down between fields. out: optional target rows (e.g. a VideoFrame
    plane) to save one more copy. OpenCV averages (SIMD, GIL released): two 1080 fields take about
    2.6 ms in UYVY and 7 ms in BGRA, a quarter of the NumPy version."""
    height=image.shape[0]
    field=image[parity::2]
    if out is None:out=np.empty_like(image)
    out[parity::2]=field
    between=cv2.addWeighted(field[:-1],0.5,field[1:],0.5,0)
    if parity==0:
        out[1:height-1:2]=between;out[height-1]=field[-1]
    else:
        out[2::2]=between;out[0]=field[0]
    return out


def pack_uyvy(data):
    height,row=data.shape;width=row//2
    frame=av.VideoFrame(width,height,'uyvy422');plane=frame.planes[0]
    if plane.line_size==row:plane.update(data)
    else:
        for y in range(height):C.memmove(plane.buffer_ptr+y*plane.line_size,data.ctypes.data+y*row,row)
    return frame


class SDI:
    def discover(self):
        return [dict(kind='sdi',name=d['name'],address=str(d['index']),busy=int(d['input_busy'])) for d in discover() if d['input']]
    def receiver(self,source):return IsolatedSdiReceiver(int(source['address']))
    def close(self):pass

class SdiReceiver:
    def __init__(self,index):
        self.input,self.name=select(index,'IDeckLinkInput_v14_2_1')
        available=modes(self.input)
        initial=next((m for m in available if m['width']==1280 and m['height']==720 and Fraction(m['fps_n'],m['fps_d'])==50),None)
        if initial is None:self.input.close();raise ValueError('Blackmagic connector has no supported initial SDI mode')
        self.mode=initial;self.configured=False;self.changed=None;self.closed=False;self.last_video=0;self.queue=deque();self.ready=threading.Condition();self.timings=Timings();self.drops={'video':0,'audio':0}
        # Interlaced input: the second field of a picture waits here until its own time, half a frame later.
        self.pending_field=None
        self.callback=Callback('IDeckLinkInputCallback_v14_2_1',{'VideoInputFormatChanged':self._format_changed,'VideoInputFrameArrived':self._arrived})
        try:
            self.input.call('SetCallback',self.callback.pointer)
            self.input.call('EnableVideoInput',initial['id'],846624121,1)
            # Eight embedded channels (groups 1 and 2), so Setup -> Input audio can pick a pair or
            # one channel. A device that only offers two keeps the first pair.
            self.audio_channels=8
            try:self.input.call('EnableAudioInput',48000,32,8)
            except RuntimeError:
                self.audio_channels=2;self.input.call('EnableAudioInput',48000,32,2)
            self.input.call('StartStreams')
        except Exception:self.close();raise
    def _format_changed(self,events,mode,flags):
        borrowed=Com(mode,'IDeckLinkDisplayMode',False);duration=L();scale=L();borrowed.call('GetFrameRate',C.byref(duration),C.byref(scale))
        with self.ready:
            if self.configured and borrowed.call('GetDisplayMode')==self.mode['id']:return
            self.changed=dict(id=borrowed.call('GetDisplayMode'),width=borrowed.call('GetWidth'),height=borrowed.call('GetHeight'),fps_n=scale.value,fps_d=duration.value,field_dominance=borrowed.call('GetFieldDominance'))
            self.ready.notify_all()
    def _push(self,event):
        with self.ready:
            if self.closed:return
            kind=event[0];maximum=3 if kind=='video' else 12
            same=[i for i,item in enumerate(self.queue) if item[0]==kind]
            if len(same)>=maximum:
                del self.queue[same[0]];self.drops[kind]+=1
            self.queue.append(event);self.ready.notify()
    def _arrived(self,video,audio):
        if self.closed:return
        arrival=time.time_ns()//1000000
        if video:
            frame=Com(video,'IDeckLinkVideoInputFrame_v14_2_1',False)
            if not frame.call('GetFlags') & 0x80000000:
                width,height,stride=frame.call('GetWidth'),frame.call('GetHeight'),frame.call('GetRowBytes')
                if not 0<width<=8192 or not 0<height<=4320 or stride<width*2:raise ValueError('Invalid SDI video buffer')
                if frame.call('GetPixelFormat')!=846624121:raise ValueError('SDI input did not provide the requested YUV format')
                pointer=P();stamp=L();duration=L();frame.call('GetBytes',C.byref(pointer));frame.call('GetStreamTime',C.byref(stamp),C.byref(duration),10000000)
                if pointer and duration.value>0:
                    image=np.ctypeslib.as_array((C.c_uint8*(stride*height)).from_address(pointer.value)).reshape(height,stride)[:,:width*2].copy()
                    self.last_video=time.monotonic()
                    self._push(('video',image,arrival,stamp.value,Fraction(self.mode['fps_n'],self.mode['fps_d'])))
        if audio and self.last_video and time.monotonic()-self.last_video<.25:
            packet=Com(audio,'IDeckLinkAudioInputPacket',False);samples=packet.call('GetSampleFrameCount')
            if 0<samples<=48000:
                pointer=P();stamp=L();packet.call('GetBytes',C.byref(pointer));packet.call('GetPacketTime',C.byref(stamp),10000000)
                if pointer:
                    from . import sources
                    count=self.audio_channels;left,right=sources.selected_channels(count)
                    block=np.ctypeslib.as_array((C.c_int32*(samples*count)).from_address(pointer.value)).reshape(samples,count)
                    pcm=np.ascontiguousarray(block[:,(left,right)].T,dtype=np.float32)
                    pcm*=1/2147483648.0
                    self._push(('audio',pcm,arrival,stamp.value,48000))
    def _due_field(self):
        from .sources import MediaEvent
        pending=getattr(self,'pending_field',None)
        if pending is None or time.perf_counter()<pending[0]:return None
        _,field,arrival,stamp,rate=pending
        self.pending_field=None;started=time.perf_counter();frame=pack_uyvy(field)
        self.timings.add('video_sdi_frame_pack',started)
        return MediaEvent('video',frame,arrival,stamp,fps=rate,scan='i')
    def read_event(self,timeout_ms=20):
        from .sources import MediaEvent
        due=self._due_field()
        if due:return due
        if getattr(self,'pending_field',None) is not None:
            timeout_ms=max(1,min(timeout_ms,int((self.pending_field[0]-time.perf_counter())*1000)+1))
        with self.ready:
            if not self.queue and self.changed is None and not self.closed:self.ready.wait(timeout_ms/1000)
            changed,self.changed=self.changed,None
        if not changed:
            due=self._due_field()
            if due:return due
        if changed:
            self.input.call('PauseStreams');self.input.call('EnableVideoInput',changed['id'],846624121,1);self.input.call('FlushStreams')
            with self.ready:self.mode=changed;self.configured=True;self.queue.clear()
            self.pending_field=None
            self.input.call('StartStreams')
        if self.callback.error:raise RuntimeError(self.callback.error)
        with self.ready:
            if not self.queue:return None
            kind,data,arrival,stamp,rate=self.queue.popleft()
        if kind=='audio':return MediaEvent(kind,data,arrival,stamp,sample_rate=rate)
        dominance=getattr(self,'mode',{}).get('field_dominance')
        from . import sources
        if dominance in (UPPER_FIELD_FIRST,LOWER_FIELD_FIRST) and sources.KEEP_INTERLACED:
            # Settings -> Interlaced sources: keep the woven picture (25 per second).
            started=time.perf_counter();frame=pack_uyvy(data)
            self.timings.add('video_sdi_frame_pack',started)
            return MediaEvent(kind,frame,arrival,stamp,fps=rate,scan='woven')
        if dominance in (UPPER_FIELD_FIRST,LOWER_FIELD_FIRST):
            # 1080i50: two fields from two moments -> two full pictures, 20 ms apart (50 per second).
            started=time.perf_counter();first=0 if dominance==UPPER_FIELD_FIRST else 1
            data=data[:data.shape[0]//2*2]
            field_rate=rate*2;half_ms=round(500/rate);half_ticks=round(5_000_000/rate)
            frame=pack_uyvy(field_picture(data,first))
            self.pending_field=(time.perf_counter()+half_ms/1000,field_picture(data,1-first),arrival+half_ms,stamp+half_ticks,field_rate)
            self.timings.add('video_sdi_deinterlace',started)
            return MediaEvent(kind,frame,arrival,stamp,fps=field_rate,scan='i')
        # Copy into an owned YUV frame, but do not colour-convert on the
        # capture/audio drain thread. Encoder preparation owns that work.
        started=time.perf_counter();frame=pack_uyvy(data)
        self.timings.add('video_sdi_frame_pack',started)
        return MediaEvent(kind,frame,arrival,stamp,fps=rate,scan='psf' if dominance==fourcc('psf ') else '')
    def read(self):
        item=self.read_event();return (item.data,item.capture_ms) if item and item.kind=='video' else None
    def close(self):
        with self.ready:self.closed=True;self.ready.notify_all()
        if self.input:
            try:
                self.input.call('StopStreams');self.input.call('SetCallback',None);self.input.call('DisableAudioInput');self.input.call('DisableVideoInput')
            finally:self.input.close();self.input=None


# Private parent/child pipes carry descriptors and PCM, not network commands.
# Three slots cap outstanding pictures. The parent owns its returned AVFrame
# and acknowledges a slot only after copying, so capture cannot overwrite it.
SLOT_BYTES=8192*4320*2
SLOT_COUNT=3

def owned_video(memory,slot,width,height,stride,size):
    if (type(slot) is not int or not 0<=slot<SLOT_COUNT or
            not 0<width<=8192 or not 0<height<=4320 or
            stride<width*2 or size!=stride*height or size>SLOT_BYTES):
        raise ValueError('Invalid private SDI frame descriptor')
    frame=av.VideoFrame(width,height,'uyvy422')
    plane=frame.planes[0]
    if plane.line_size!=stride or plane.buffer_size!=size:
        raise ValueError('SDI child and parent frame strides differ')
    # NumPy releases the GIL for this 4 MB copy; PyAV's plane.update holds it
    # and stalls NVENC/colour threads for milliseconds.
    np.copyto(np.frombuffer(plane,np.uint8),np.frombuffer(memory.buf,np.uint8,count=size,offset=slot*SLOT_BYTES))
    return frame


class IsolatedSdiReceiver:
    def __init__(self,index):
        self.closed=False;self.error=None;self.queue=deque();self.ready=threading.Condition()
        self.copy_lock=threading.Lock();self.write_lock=threading.Lock();self.started=threading.Event()
        self.timings=Timings();self.memory=None;self.process=None;self.reader=None
        self.drops={'video':0,'audio':0};self.capture_drops={'video':0,'audio':0};self.slot_drops=0
        self.keep_sent=None;self.audio_sent=('pair',1);self.audio_channels=2
        try:
            self.memory=SharedMemory(create=True,size=SLOT_COUNT*SLOT_BYTES)
            self.process=subprocess.Popen([sys.executable,'-m','fastvideo.sdi_input',self.memory.name,str(index)],
                cwd=Path(__file__).resolve().parents[1],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            self.reader=threading.Thread(target=self._read,name='Bridge-SDI-events',daemon=True)
            self.reader.start()
            if not self.started.wait(15):raise RuntimeError('SDI capture worker did not start')
            if self.error:raise RuntimeError(self.error)
        except BaseException:
            self.close();raise
    def _send(self,value):
        from .isolated_encoder import send
        with self.write_lock:
            if self.process and self.process.poll() is None:
                try:send(self.process.stdin,value)
                except (BrokenPipeError,OSError):pass
    def _read(self):
        from .isolated_encoder import receive
        try:
            while not self.closed:
                event=receive(self.process.stdout)
                if event[0]=='ready':
                    if len(event)>1 and type(event[1]) is int:self.audio_channels=event[1]
                    self.started.set();continue
                if event[0]=='error':raise RuntimeError(event[1])
                if event[0]=='counters':
                    with self.ready:self.capture_drops=dict(event[1]);self.slot_drops=event[2]
                    continue
                if event[0] not in ('video','audio'):raise ValueError('Invalid SDI worker event')
                dropped=[]
                with self.ready:
                    maximum=3 if event[0]=='video' else 12
                    same=[i for i,item in enumerate(self.queue) if item[0]==event[0]]
                    if len(same)>=maximum:
                        dropped.append(self.queue[same[0]]);del self.queue[same[0]]
                        self.drops[event[0]]+=1
                    self.queue.append(event);self.ready.notify()
                for item in dropped:
                    if item[0]=='video':self._send(('free',item[1]))
        except Exception as error:
            if not self.closed:
                with self.ready:self.error=str(error) or 'SDI capture worker ended';self.ready.notify_all()
        finally:self.started.set()
    def read_event(self,timeout_ms=20):
        from .sources import MediaEvent
        from . import sources
        if sources.KEEP_INTERLACED!=getattr(self,'keep_sent',False):
            # Settings -> Interlaced sources is applied in the capture worker, which splits the fields.
            self.keep_sent=sources.KEEP_INTERLACED;self._send(('keep_interlaced',self.keep_sent))
        if sources.AUDIO_SELECT!=getattr(self,'audio_sent',('pair',1)):
            # Setup -> Input audio: the capture worker picks the channels out of the SDI packet.
            self.audio_sent=sources.AUDIO_SELECT;self._send(('audio_select',)+tuple(self.audio_sent))
        with self.copy_lock:
            with self.ready:
                if not self.queue and not self.closed and not self.error:self.ready.wait(timeout_ms/1000)
                if self.error:raise RuntimeError(self.error)
                if self.closed or not self.queue:return None
                event=self.queue.popleft()
            if event[0]=='audio':
                _,data,samples,arrival,stamp,rate=event
                if not 0<samples<=48000 or len(data)!=samples*2*4:raise ValueError('Invalid private SDI PCM')
                pcm=np.frombuffer(data,dtype=np.float32).reshape(2,samples).copy()
                sources.AUDIO_CHANNELS=getattr(self,'audio_channels',2)
                return MediaEvent('audio',pcm,arrival,stamp,sample_rate=rate)
            _,slot,width,height,stride,size,arrival,stamp,rate,*rest=event;scan=rest[0] if rest else ''
            started=time.perf_counter()
            try:frame=owned_video(self.memory,slot,width,height,stride,size)
            finally:self._send(('free',slot))
            self.timings.add('video_sdi_ipc_receive_and_pack',started)
            return MediaEvent('video',frame,arrival,stamp,fps=rate,scan=scan)
    def read(self):
        item=self.read_event();return (item.data,item.capture_ms) if item and item.kind=='video' else None
    def capture_stats(self):
        with self.ready:
            return {'native_queue_drops':dict(self.capture_drops),
                'transfer_slot_drops':self.slot_drops,'parent_queue_drops':dict(self.drops)}
    def close(self):
        with self.copy_lock:
            with self.ready:self.closed=True;self.ready.notify_all()
            if self.process:
                self._send(('stop',))
                try:self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=3)
                if self.reader:self.reader.join(timeout=1)
                for stream in (self.process.stdin,self.process.stdout):stream.close()
                self.process=None
            if self.memory:self.memory.close();self.memory.unlink();self.memory=None


def capture_worker(name,index):
    from .isolated_encoder import send,receive
    memory=SharedMemory(name=name);receiver=None;stop=threading.Event()
    slots=deque(range(SLOT_COUNT));busy=set();lock=threading.Lock();slot_drops=0;reported=0
    def controls():
        try:
            while not stop.is_set():
                value=receive(sys.stdin.buffer)
                if value==('stop',):break
                if len(value)==2 and value[0]=='keep_interlaced' and type(value[1]) is bool:
                    from . import sources
                    sources.KEEP_INTERLACED=value[1];continue
                if len(value)==3 and value[0]=='audio_select':
                    from . import sources
                    if sources.valid_audio_selection(value[1],value[2]):sources.AUDIO_SELECT=(value[1],value[2])
                    continue
                if len(value)!=2 or value[0]!='free' or type(value[1]) is not int:raise ValueError('Invalid SDI acknowledgement')
                with lock:
                    if value[1] in busy:busy.remove(value[1]);slots.append(value[1])
        except (EOFError,OSError,ValueError):pass
        finally:stop.set()
    threading.Thread(target=controls,name='Bridge-SDI-slot-release',daemon=True).start()
    try:
        receiver=SdiReceiver(index)
        send(sys.stdout.buffer,('ready',getattr(receiver,'audio_channels',2)))
        while not stop.is_set():
            item=receiver.read_event(20)
            now=time.monotonic()
            if now-reported>=1:
                with receiver.ready:counts=dict(receiver.drops)
                send(sys.stdout.buffer,('counters',counts,slot_drops));reported=now
            if item is None:continue
            if item.kind=='audio':
                send(sys.stdout.buffer,('audio',item.data.tobytes(),item.data.shape[1],item.capture_ms,item.source_time,item.sample_rate))
                continue
            plane=item.data.planes[0]
            if plane.buffer_size>SLOT_BYTES:raise ValueError('SDI frame exceeds private capture slot')
            with lock:slot=slots.popleft() if slots else None
            if slot is None:slot_drops+=1;continue
            with lock:busy.add(slot)
            memory.buf[slot*SLOT_BYTES:slot*SLOT_BYTES+plane.buffer_size]=memoryview(plane)
            send(sys.stdout.buffer,('video',slot,item.data.width,item.data.height,plane.line_size,plane.buffer_size,item.capture_ms,item.source_time,item.fps,item.scan))
    except Exception as error:
        try:send(sys.stdout.buffer,('error',str(error)))
        except (OSError,BrokenPipeError):pass
    finally:
        stop.set()
        if receiver:receiver.close()
        memory.close()


if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit('Private SDI worker requires its memory handle and connector index')
    from .process_priority import opt_out
    opt_out()
    capture_worker(sys.argv[1],int(sys.argv[2]))
