"""Owned VMX encoder for NV12 output, using the public bundled codec API."""
import ctypes as C
import os
from pathlib import Path
import numpy as np
from .sources import bind

class Size(C.Structure):
    _fields_=[('width',C.c_int),('height',C.c_int)]

class VmxOutput:
    def __init__(self,width,height,colorspace):
        path=Path(__file__).resolve().parents[1]/'vendor'/'omt'/'libvmx.dll'
        self.dll=C.CDLL(str(path));self.handle=None
        create=bind(self.dll,'VMX_Create',[Size,C.c_int,C.c_int],C.c_void_p)
        self.destroy=bind(self.dll,'VMX_Destroy',[C.c_void_p],None)
        self.encode=bind(self.dll,'VMX_EncodeNV12',[C.c_void_p,C.c_void_p,C.c_int,C.c_void_p,C.c_int,C.c_int],C.c_int)
        self.save=bind(self.dll,'VMX_SaveTo',[C.c_void_p,C.c_void_p,C.c_int],C.c_int)
        self.get_quality=bind(self.dll,'VMX_GetQuality',[C.c_void_p],C.c_int)
        self.set_quality=bind(self.dll,'VMX_SetQuality',[C.c_void_p,C.c_int],None)
        getthreads=bind(self.dll,'VMX_GetThreads',[C.c_void_p],C.c_int)
        setthreads=bind(self.dll,'VMX_SetThreads',[C.c_void_p,C.c_int],None)
        self.signature=(width,height,colorspace)
        # OMT_HQ is exactly the profile selected by omt_send_create(..., 100).
        self.handle=create(Size(width,height),199,colorspace)
        if not self.handle:raise RuntimeError('VMX encoder could not start')
        try:
            self.default_threads=getthreads(self.handle)
            if self.default_threads<4:
                setthreads(self.handle,max(self.default_threads,min(4,os.cpu_count() or 1)))
            self.threads=getthreads(self.handle)
            self.buffer=np.empty(width*height*4,np.uint8)
        except BaseException:
            self.close();raise

    def packet(self,frame):
        if frame.format.name!='nv12':raise ValueError('VMX direct output requires NV12')
        y,uv=frame.planes
        if (frame.width,frame.height)!=self.signature[:2]:raise ValueError('VMX surface size changed')
        if frame.width%2 or frame.height%2:raise ValueError('VMX direct output requires even dimensions')
        if y.line_size<frame.width or uv.line_size<frame.width or y.buffer_size<y.line_size*frame.height or uv.buffer_size<uv.line_size*(frame.height//2):
            raise ValueError('Truncated NV12 surface')
        # Both planes stay owned by the awaited video worker until Encode returns.
        result=self.encode(self.handle,y.buffer_ptr,y.line_size,uv.buffer_ptr,uv.line_size,0)
        if result!=0:raise RuntimeError('VMX encoding failed: '+str(result))
        length=self.save(self.handle,self.buffer.ctypes.data,self.buffer.size)
        if not 0<length<=self.buffer.size:raise RuntimeError('VMX packet size is invalid')
        return self.buffer.ctypes.data,length

    def close(self):
        if self.handle:self.destroy(self.handle);self.handle=None
