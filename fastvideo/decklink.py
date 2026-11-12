"""Small Windows COM binding for the installed Blackmagic Desktop Video driver.

Only ABI descriptions are bundled; Blackmagic's driver stays a user installation.
Discovery is read-only and never changes connector profiles or routing.
"""
import ctypes as C
import json
import threading
import uuid
from pathlib import Path

ABI=json.loads(Path(__file__).with_name('decklink_abi.json').read_text(encoding='utf-8'))
P=C.c_void_p
I=C.c_int32
U=C.c_uint32
L=C.c_int64
TYPES=dict(pointer=P,int32=I,uint32=U,int64=L,double=C.c_double)
_thread=threading.local()
_ole=C.OleDLL('ole32')
# WinDLL, not OleDLL: OleDLL raises on every failing HRESULT whatever restype says, so the accepted
# RPC_E_CHANGED_MODE (thread already initialised by another library, e.g. in a shared worker thread)
# would abort SDI discovery.
_coinit=C.WinDLL('ole32').CoInitializeEx
_coinit.argtypes=[P,U]
_coinit.restype=I
_ole.CoCreateInstance.argtypes=[P,P,U,P,C.POINTER(P)]
_ole.CoCreateInstance.restype=I
_aut=C.OleDLL('oleaut32')
_aut.SysStringLen.argtypes=[P];_aut.SysStringLen.restype=U
_aut.SysFreeString.argtypes=[P];_aut.SysFreeString.restype=None

def guid(value):return (C.c_ubyte*16).from_buffer_copy(uuid.UUID(value).bytes_le)
def apartment():
    if getattr(_thread,'sdk_callback',False):return
    if not getattr(_thread,'initialized',False):
        result=_coinit(None,0)
        if result<0 and result!=-2147417850:raise RuntimeError('Windows COM initialization failed')
        # Worker threads are long lived; no interfaces cross an STA boundary.
        _thread.initialized=True
def check(result,action):
    if result<0:raise RuntimeError(f'Blackmagic {action} failed (0x{result & 0xffffffff:08X}). Check that the connector is free and supports this format.')

class Com:
    def __init__(self,pointer,kind,owned=True):
        self.pointer=P(pointer.value if isinstance(pointer,P) else pointer)
        self.kind=kind;self.owned=owned;self.functions={}
    def raw(self,slot,result,args):
        if not self.pointer:raise RuntimeError('Blackmagic interface is closed')
        table=C.cast(self.pointer,C.POINTER(C.POINTER(P))).contents
        return C.WINFUNCTYPE(result,P,*args)(table[slot])
    def call(self,name,*args):
        apartment()
        if name not in self.functions:
            methods=ABI[self.kind]['methods'];index=next(i for i,item in enumerate(methods) if item['name']==name)
            method=methods[index]
            self.functions[name]=(self.raw(index+3,TYPES[method['result']],[TYPES[t] for t in method['args']]),method['hresult'])
        function,hresult=self.functions[name]
        result=function(self.pointer,*args)
        if hresult:check(result,name)
        return result
    def query(self,kind,optional=False):
        value=P();iid=guid(ABI[kind]['iid'])
        result=self.raw(0,I,[P,P])(self.pointer,C.byref(iid),C.byref(value))
        if result<0:
            if optional:return None
            check(result,'QueryInterface '+kind)
        return Com(value,kind)
    def interface(self,method,kind,*args):
        value=P();result=self.call(method,*args,C.byref(value))
        return Com(value,kind) if result==0 and value else None
    def string(self,name):
        value=P();self.call(name,C.byref(value))
        try:return C.wstring_at(value,_aut.SysStringLen(value)) if value else ''
        finally:
            if value:_aut.SysFreeString(value)
    def close(self):
        if self.pointer and self.owned:
            self.raw(2,U,[])(self.pointer)
        self.pointer=P()
    def __del__(self):
        try:self.close()
        except Exception:pass

def devices():
    apartment();value=P();cls=guid(ABI['iterator_class']);iid=guid(ABI['IDeckLinkIterator']['iid'])
    check(_ole.CoCreateInstance(C.byref(cls),None,1,C.byref(iid),C.byref(value)),'device discovery; install Blackmagic Desktop Video')
    iterator=Com(value,'IDeckLinkIterator');result=[]
    try:
        while True:
            device=iterator.interface('Next','IDeckLink')
            if device is None:break
            result.append(device)
    finally:iterator.close()
    return result

BUSY,DUPLEX,FULL_DUPLEX=1651864441,int.from_bytes(b'dupx','big'),int.from_bytes(b'dxfu','big')
CAPTURE_BUSY,PLAYBACK_BUSY=1,2

def direction_busy(device,busy,capture):
    """True when this direction of a device cannot be opened. A full-duplex device (one connector
    pair for input and output, e.g. UltraStudio HD Mini) captures while it plays out: only the same
    direction blocks. On any other device the opposite direction blocks too. busy: the driver's
    busy flags (1 capture, 2 playback, 4 serial port)."""
    if not busy:return False
    if busy&(CAPTURE_BUSY if capture else PLAYBACK_BUSY):return True
    if not busy&(CAPTURE_BUSY|PLAYBACK_BUSY):return False
    attributes=device.query('IDeckLinkProfileAttributes',True)
    if not attributes:return True
    try:
        duplex=L();attributes.call('GetInt',DUPLEX,C.byref(duplex))
        return duplex.value!=FULL_DUPLEX
    except RuntimeError:return True
    finally:attributes.close()

def select(index,kind):
    if type(index) is not int or index<0:raise ValueError('Choose a Blackmagic SDI connector')
    all_devices=devices()
    try:
        if index>=len(all_devices):raise ValueError('Selected Blackmagic connector is no longer available')
        device=all_devices[index]
        name=device.string('GetDisplayName')
        status=device.query('IDeckLinkStatus',True)
        if status:
            try:
                busy=L();status.call('GetInt',BUSY,C.byref(busy))
                if direction_busy(device,busy.value,'Input' in kind):raise ValueError('Selected Blackmagic connector is already in use')
            finally:status.close()
        return device.query(kind),name
    finally:
        for device in all_devices:device.close()

def modes(interface):
    iterator=interface.interface('GetDisplayModeIterator','IDeckLinkDisplayModeIterator');result=[]
    try:
        while True:
            mode=iterator.interface('Next','IDeckLinkDisplayMode')
            if mode is None:break
            try:
                duration=L();scale=L();mode.call('GetFrameRate',C.byref(duration),C.byref(scale))
                result.append(dict(id=mode.call('GetDisplayMode'),name=mode.string('GetName'),width=mode.call('GetWidth'),height=mode.call('GetHeight'),fps_n=scale.value,fps_d=duration.value,field_dominance=mode.call('GetFieldDominance')))
            finally:mode.close()
    finally:
        if iterator:iterator.close()
    return result

def discover():
    all_devices=devices();result=[]
    try:
        for index,device in enumerate(all_devices):
            status=device.query('IDeckLinkStatus',True);busy=None
            if status:
                try:value=L();status.call('GetInt',1651864441,C.byref(value));busy=value.value
                finally:status.close()
            inputs=device.query('IDeckLinkInput_v14_2_1',True);outputs=device.query('IDeckLinkOutput_v14_2_1',True)
            try:
                result.append(dict(index=index,name=device.string('GetDisplayName'),busy=busy,
                    input_busy=direction_busy(device,busy or 0,True),output_busy=direction_busy(device,busy or 0,False),input=inputs is not None,output=outputs is not None,input_modes=modes(inputs) if inputs else [],output_modes=modes(outputs) if outputs else []))
            finally:
                if inputs:inputs.close()
                if outputs:outputs.close()
    finally:
        for device in all_devices:device.close()
    return result

class Callback:
    """IUnknown callback whose vtable and Python closures stay alive until Stop."""
    def __init__(self,kind,handlers):
        self.references=1;self.kind=kind
        def query(this,iid,value):
            requested=C.string_at(iid,16)
            supported=[bytes(guid(ABI[kind]['iid'])),bytes(guid('00000000-0000-0000-c000-000000000046'))]
            result=C.cast(value,C.POINTER(P));result[0]=None
            if requested not in supported:return -2147467262
            result[0]=self.pointer;self.references+=1;return 0
        def add(this):self.references+=1;return self.references
        def release(this):self.references=max(0,self.references-1);return self.references
        self.functions=[C.WINFUNCTYPE(I,P,P,P)(query),C.WINFUNCTYPE(U,P)(add),C.WINFUNCTYPE(U,P)(release)]
        self.error=''
        for method in ABI[kind]['methods']:
            handler=handlers[method['name']]
            def invoke(this,*args,handler=handler):
                _thread.sdk_callback=True
                try:handler(*args);return 0
                except Exception as error:self.error=str(error);return 0
                finally:_thread.sdk_callback=False
            self.functions.append(C.WINFUNCTYPE(I,P,*[TYPES[t] for t in method['args']])(invoke))
        self.table=(P*len(self.functions))(*[C.cast(fn,P).value for fn in self.functions])
        self.instance=(P*1)(C.cast(self.table,P).value)
        self.pointer=C.cast(self.instance,P)
