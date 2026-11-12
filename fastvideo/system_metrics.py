"""Nonblocking Windows host CPU and physical memory measurements."""
import ctypes
from ctypes import wintypes
import time
import winreg

class DisplayDevice(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('name',wintypes.WCHAR*32),
              ('description',wintypes.WCHAR*128),('flags',wintypes.DWORD),
              ('device_id',wintypes.WCHAR*128),('key',wintypes.WCHAR*128)]

def hardware_names():
    # Read once at startup. No WMI process or hardware polling in status requests.
    result={'cpu_name':None,'gpu_name':None,'gpu_names':[]}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
            result['cpu_name']=winreg.QueryValueEx(key,'ProcessorNameString')[0].strip()
    except OSError:pass
    try:
        enumerate_devices=ctypes.WinDLL('user32',use_last_error=True).EnumDisplayDevicesW
        enumerate_devices.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,ctypes.POINTER(DisplayDevice),wintypes.DWORD]
        enumerate_devices.restype=wintypes.BOOL
        active=[]
        for index in range(64):
            device=DisplayDevice();device.size=ctypes.sizeof(device)
            if not enumerate_devices(None,index,ctypes.byref(device),0):break
            if device.flags & 8:continue # Mirroring driver, not a GPU.
            name=device.description.strip()
            if name and name not in result['gpu_names']:result['gpu_names'].append(name)
            if name and device.flags & 1:
                if device.flags & 4:active.insert(0,name) # Primary display adapter.
                else:active.append(name)
        result['gpu_name']=(active or result['gpu_names'] or [None])[0]
    except OSError:pass
    return result

class MemoryStatus(ctypes.Structure):
    _fields_=[("length",wintypes.DWORD),("load",wintypes.DWORD)]+[(name,ctypes.c_ulonglong) for name in
        ("total_physical","available_physical","total_pagefile","available_pagefile","total_virtual","available_virtual","available_extended")]

class SystemMetrics:
    def __init__(self):
        self.kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        self.kernel.GetSystemTimes.argtypes=[ctypes.POINTER(wintypes.FILETIME)]*3
        self.kernel.GetSystemTimes.restype=wintypes.BOOL
        self.kernel.GlobalMemoryStatusEx.argtypes=[ctypes.POINTER(MemoryStatus)]
        self.kernel.GlobalMemoryStatusEx.restype=wintypes.BOOL
        self.previous=self.cpu_times()
        self.last_at=0
        self.cached=None
        self.hardware=hardware_names()

    def cpu_times(self):
        values=[wintypes.FILETIME() for _ in range(3)]
        if not self.kernel.GetSystemTimes(*(ctypes.byref(v) for v in values)):
            raise ctypes.WinError(ctypes.get_last_error())
        return tuple((v.dwHighDateTime<<32)|v.dwLowDateTime for v in values)

    @staticmethod
    def cpu_percent(current,previous):
        idle=current[0]-previous[0]
        total=(current[1]-previous[1])+(current[2]-previous[2])
        return round(max(0,min(100,(total-idle)*100/total)),1) if total>0 else None

    def snapshot(self):
        now=time.monotonic()
        if self.cached is not None and now-self.last_at<1:return dict(self.cached)
        current=self.cpu_times()
        memory=MemoryStatus();memory.length=ctypes.sizeof(memory)
        if not self.kernel.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.cached={"cpu_percent":self.cpu_percent(current,self.previous),
            **self.hardware,
            "ram_used_bytes":memory.total_physical-memory.available_physical,
            "ram_total_bytes":memory.total_physical}
        self.previous=current;self.last_at=now
        return dict(self.cached)
