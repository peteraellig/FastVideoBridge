"""Saved output forms; Windows DPAPI protects tokens and passphrases at rest."""
import base64
import ctypes
from ctypes import wintypes
import json

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_byte))]

def protect(value,decode=False):
    raw=base64.b64decode(value) if decode else value.encode('utf-8')
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_byte)))
    result=Blob()
    crypto=ctypes.WinDLL('crypt32',use_last_error=True)
    call=crypto.CryptUnprotectData if decode else crypto.CryptProtectData
    call.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    call.restype=wintypes.BOOL
    if not call(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:output=ctypes.string_at(result.data,result.size)
    finally:
        free=ctypes.WinDLL('kernel32').LocalFree
        free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
        free(result.data)
    return output.decode('utf-8') if decode else base64.b64encode(output).decode('ascii')

class OutputSettings:
    def __init__(self,path):self.path=path
    def load(self):
        if not self.path.exists():return {}
        data=json.loads(self.path.read_text(encoding='utf-8-sig'))
        for kind,field in (('whip','token'),('srt','passphrase'),('receive','token'),('receive','viewer_url'),('receive','whep_token')):
            if data.get(kind,{}).get(field):
                # DPAPI secrets only open for the Windows account that saved them.
                # A copied installation must ask for them again, not fail every request.
                try:data[kind][field]=protect(data[kind][field],True)
                except OSError:data[kind][field]=''
        return data
    def save(self,kind,config):
        data=self.load();data[kind]=dict(config)
        self.replace(data)

    def replace(self,data):
        data=json.loads(json.dumps(data))
        for output,field in (('whip','token'),('srt','passphrase'),('receive','token'),('receive','viewer_url'),('receive','whep_token')):
            if data.get(output,{}).get(field):data[output][field]=protect(data[output][field])
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data,indent=2),encoding='utf-8')
        temporary.replace(self.path)
