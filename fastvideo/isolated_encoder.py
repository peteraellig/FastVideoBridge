"""Private bounded IPC; GPU initialization never runs in the capture process."""
import os
import pickle
import queue
import struct
import subprocess
import sys
import threading
from multiprocessing.shared_memory import SharedMemory
import time
from collections import deque
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
import av
from .diagnostics import Timings


def send(stream, value):
    data=pickle.dumps(value,protocol=5)
    stream.write(struct.pack('!I',len(data))+data);stream.flush()


def receive(stream):
    def exact(count):
        result=bytearray()
        while len(result)<count:
            part=stream.read(count-len(result))
            if not part:raise EOFError()
            result.extend(part)
        return bytes(result)
    size=struct.unpack('!I',exact(4))[0]
    if size>32*1024*1024:raise ValueError('Invalid private encoder message')
    return pickle.loads(exact(size))


class IsolatedEncoder:
    def __init__(self,kind, fps=50,bitrate=4000000,preset='medium',raw=False,keyframe=1,cbr=False,profile='high',backend='qsv',nvenc_preset=None):
        self.config=dict(kind=kind,fps=fps,bitrate=bitrate,preset=preset,raw=raw,keyframe=keyframe,cbr=cbr,profile=profile,backend=backend,nvenc_preset=nvenc_preset)
        self.target_bitrate=bitrate
        self.codec=SimpleNamespace(gop_size=round(fps*keyframe))
        self.timings=Timings();self.encode_times=deque(maxlen=100)
        self.process=None;self.memory=None;self.responses=queue.Queue(maxsize=2);self.format=None
    def _launch(self,frame):
        self.responses=queue.Queue(maxsize=2)
        self.memory=SharedMemory(create=True,size=sum(p.buffer_size for p in frame.planes))
        self.config['memory']=self.memory.name
        self.process=subprocess.Popen([sys.executable,'-m','fastvideo.isolated_encoder'],cwd=Path(__file__).resolve().parents[1],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        process=self.process
        responses=self.responses
        def read():
            try:
                while True:responses.put(receive(process.stdout),timeout=1)
            except (EOFError,OSError,ValueError,queue.Full):
                try:responses.put_nowait((False,'Encoder worker ended'))
                except queue.Full:pass
        threading.Thread(target=read,daemon=True,name='Bridge-encoder-results').start()
        send(self.process.stdin,self.config)
    def encode(self,frame,force_keyframe=False):
        fps=Fraction(frame.opaque) if frame.opaque is not None else Fraction(self.config['fps'])
        signature=(frame.width,frame.height,fps)
        if signature!=self.format:
            self.close()
            self.config['fps']=fps
            self.codec.gop_size=round(fps*self.config['keyframe'])
            self.format=signature
            force_keyframe=True
        if self.process is not None and sum(p.buffer_size for p in frame.planes)>self.memory.size:self.close()
        if self.process is None:self._launch(frame)
        if frame.format.name!='nv12':raise ValueError('Encoder requires shared NV12 input')
        started=time.perf_counter()
        planes=[];offset=0
        for plane in frame.planes:
            self.memory.buf[offset:offset+plane.buffer_size]=memoryview(plane)
            planes.append((plane.line_size,offset,plane.buffer_size));offset+=plane.buffer_size
        send(self.process.stdin,(frame.width,frame.height,frame.pts,frame.time_base,force_keyframe,planes))
        try:ok,result=self.responses.get(timeout=10)
        except queue.Empty:
            self.close();raise RuntimeError('Hardware encoder worker timed out')
        if not ok:raise RuntimeError(result)
        self.encode_times.append((time.perf_counter()-started)*1000);self.timings.add('isolated_encode_including_ipc',started)
        return result
    def close(self):
        if self.process:
            self.process.terminate()
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
            for stream in (self.process.stdin,self.process.stdout):stream.close()
            self.process=None
        if self.memory:
            self.memory.close();self.memory.unlink();self.memory=None


def worker():
    config=receive(sys.stdin.buffer)
    memory=SharedMemory(name=config['memory'])
    encoder=None
    while True:
        try:width,height,pts,base,force,planes=receive(sys.stdin.buffer)
        except EOFError:
            memory.close();return
        try:
            frame=av.VideoFrame(width,height,'nv12')
            for destination,(stride,offset,size) in zip(frame.planes,planes):
                data=memory.buf[offset:offset+size]
                if stride==destination.line_size:destination.update(data)
                else:
                    packed=bytearray(destination.buffer_size)
                    for row in range(destination.height):
                        packed[row*destination.line_size:row*destination.line_size+width]=data[row*stride:row*stride+width]
                    destination.update(packed)
                del data
            frame.pts,frame.time_base=pts,base
            if encoder is None:
                if config['raw']:
                    from .srt_output import native_context
                    encoder=native_context(config['kind'],config['bitrate'],config['fps'],width,height,config['keyframe'],config['cbr'],config['preset'],config.get('backend','qsv'))
                    if config.get('backend','qsv')=='qsv':encoder.options['forced_idr']='1'
                elif config['kind']=='av1':
                    from .av1 import Av1Encoder
                    encoder=Av1Encoder(config['fps'],config['bitrate'],backend='qsv',qsv_preset=config['preset'])
                elif config.get('backend','qsv')!='qsv':
                    if config['backend']=='nvenc':
                        from .encoder import NvencEncoder
                        nvenc_p=config.get('nvenc_preset') or {'medium':'p4','slow':'p5','veryslow':'p6'}.get(config['preset'],'p4')
                        encoder=NvencEncoder(config['fps'],config['profile'],nvenc_p)
                    else:
                        from aiortc.codecs.h264 import H264Encoder
                        encoder=H264Encoder()
                        context=av.CodecContext.create('libx264','w')
                        context.width,context.height,context.pix_fmt=width,height,'yuv420p'
                        context.framerate,context.time_base=Fraction(config['fps']),Fraction(1,90000)
                        context.bit_rate=config['bitrate'];context.gop_size=round(config['fps']*config['keyframe']);context.max_b_frames=0
                        context.options={'preset':'veryfast','tune':'zerolatency','profile':config['profile']}
                        encoder.codec=context
                    encoder.target_bitrate=config['bitrate']
                else:
                    from .h264_qsv import QsvH264Encoder
                    encoder=QsvH264Encoder(config['fps'],config['bitrate'],config['preset'],config['profile'])
            if config['raw']:
                frame.pict_type=av.video.frame.PictureType.I if force else av.video.frame.PictureType.NONE
                result=[(bytes(p),bool(p.is_keyframe),int(p.pts*p.time_base*90000)) for p in encoder.encode(frame)]
            else:result=encoder.encode(frame,force)
            send(sys.stdout.buffer,(True,result))
        except Exception:
            # The parent discards this process's stderr: keep the reason in the work folder.
            import traceback
            try:
                with open(Path(__file__).resolve().parents[1]/'work'/'isolated-encoder.err','a',encoding='utf-8') as log:
                    traceback.print_exc(file=log)
            except OSError:pass
            send(sys.stdout.buffer,(False,'Hardware encoding failed'))
            memory.close();return

if __name__=='__main__':
    from .process_priority import opt_out
    opt_out()
    worker()
