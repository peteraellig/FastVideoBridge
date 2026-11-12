"""Keep Windows from throttling the windowless Bridge processes as background work.

Started from Explorer, the hidden Bridge processes were treated as background
work and moved to efficiency cores: colour conversion took 4.4 ms instead of 0.9 ms.
"""
import ctypes
import os

PROCESS_POWER_THROTTLING = 4
EXECUTION_SPEED = 0x1
IGNORE_TIMER_RESOLUTION = 0x4   # Windows 11 and later


class _ThrottlingState(ctypes.Structure):
    _fields_ = [('Version', ctypes.c_ulong), ('ControlMask', ctypes.c_ulong), ('StateMask', ctypes.c_ulong)]


def opt_out(handle=None):
    """Opt the current process (or the given process handle) out of power throttling."""
    if os.name != 'nt':
        return False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    kernel.SetProcessInformation.restype = ctypes.c_int
    target = ctypes.c_void_p(int(handle)) if handle is not None else kernel.GetCurrentProcess()
    # ControlMask selects the policies; StateMask 0 means "not throttled".
    for mask in (EXECUTION_SPEED | IGNORE_TIMER_RESOLUTION, EXECUTION_SPEED):
        state = _ThrottlingState(1, mask, 0)
        if kernel.SetProcessInformation(target, PROCESS_POWER_THROTTLING, ctypes.byref(state), ctypes.sizeof(state)):
            return True
    return False
