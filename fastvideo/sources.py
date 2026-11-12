"""Read-only NDI/OMT discovery and video reception via their native C APIs."""
from fractions import Fraction
import ctypes as C
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import threading
import time

import cv2
import numpy as np
from .diagnostics import Timings

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class MediaEvent:
    kind: str
    data: object  # Owned ndarray, or an owned native SDI YUV VideoFrame.
    capture_ms: int
    source_time: int  # 100 ns, one source clock for audio and video
    sample_rate: int = 0
    gain: float = 1.0
    fps: float = 0.0
    scan: str = ''  # SDI: 'i' = interlaced, sent as fields; 'psf' = segmented frame; '' = progressive


class NDIAudio(C.Structure):
    _fields_ = [("sample_rate", C.c_int), ("channels", C.c_int), ("samples", C.c_int),
                ("timecode", C.c_int64), ("data", C.c_void_p), ("stride", C.c_int),
                ("metadata", C.c_char_p), ("timestamp", C.c_int64)]


# Input audio selection (Setup -> Input audio), as in FastVideo: ('pair', n) sends channels
# 2n-1 / 2n as left / right, ('mono', n) sends channel n on both. Applied to every source packet,
# so a change is heard at once. AUDIO_CHANNELS: channels of the running source (0 = none seen yet).
AUDIO_SELECT = ('pair', 1)
AUDIO_CHANNELS = 0


def valid_audio_selection(mode, index):
    return mode in ('pair', 'mono') and type(index) is int and 1 <= index <= (16 if mode == 'pair' else 32)


def selected_channels(channels, selection=None):
    """Channel indexes (left, right) for a source with this many channels. A selection the
    source does not have falls back to the first pair, so a wrong setting is never silence."""
    mode, index = selection or AUDIO_SELECT
    if mode == 'mono' and index <= channels:
        return index-1, index-1
    if mode == 'pair' and index*2 <= channels:
        return index*2-2, index*2-1
    return (0, 1) if channels >= 2 else (0, 0)


def copy_audio(data, samples, channels, stride):
    global AUDIO_CHANNELS
    if not data or not 1 <= channels <= 32 or not 1 <= samples <= 192000 or stride < samples * 4:
        raise ValueError("Ungültiger Audio-Puffer")
    AUDIO_CHANNELS = channels
    planes = [np.ctypeslib.as_array((C.c_float*samples).from_address(data+c*stride)).copy()
              for c in selected_channels(channels)]
    return np.stack(planes)


class NDISource(C.Structure):
    _fields_ = [("name", C.c_char_p), ("url", C.c_char_p)]


class NDIFindSettings(C.Structure):
    _fields_ = [("show_local", C.c_bool), ("groups", C.c_char_p), ("extra_ips", C.c_char_p)]


class NDIRecvSettings(C.Structure):
    _fields_ = [("source", NDISource), ("color_format", C.c_int),
                ("bandwidth", C.c_int), ("allow_fields", C.c_bool), ("name", C.c_char_p)]


class NDIVideo(C.Structure):
    _fields_ = [("width", C.c_int), ("height", C.c_int), ("fourcc", C.c_uint32),
                ("fps_n", C.c_int), ("fps_d", C.c_int), ("aspect", C.c_float),
                ("frame_format", C.c_int), ("timecode", C.c_int64),
                ("data", C.c_void_p), ("stride", C.c_int),
                ("metadata", C.c_char_p), ("timestamp", C.c_int64)]


OMT_INTERLACED = 1  # OMTVideoFlags_Interlaced (libomt.h)

# Settings -> Interlaced sources. False: both fields of a 1080i picture become two full pictures (50p,
# for browsers and progressive outputs). True: the picture stays as it is, both fields woven, 25 per
# second; a receiver with 'SDI 1080 interlaced' outputs it as the original 1080i50. No splitting, no
# re-pairing, about half the work (.102 could not split 1080i in time).
KEEP_INTERLACED = False


def woven_frame(interlaced, height, fps):
    """NDI/OMT outputs: a 1080 picture at a frame rate (25 woven frames from a sender with 'keep
    interlaced') leaves marked as interlaced, so vMix sees 1080i50. 50p stays progressive there."""
    return bool(interlaced and height == 1080 and fps and Fraction(fps).limit_denominator(1001) in
                (Fraction(25), Fraction(30000, 1001), Fraction(30)))


class OMTFrame(C.Structure):
    _fields_ = [("type", C.c_int), ("timestamp", C.c_int64), ("codec", C.c_int),
                ("width", C.c_int), ("height", C.c_int), ("stride", C.c_int),
                ("flags", C.c_int), ("fps_n", C.c_int), ("fps_d", C.c_int),
                ("aspect", C.c_float), ("colorspace", C.c_int),
                ("sample_rate", C.c_int), ("channels", C.c_int), ("samples", C.c_int),
                ("data", C.c_void_p), ("data_length", C.c_int),
                ("compressed", C.c_void_p), ("compressed_length", C.c_int),
                ("metadata", C.c_void_p), ("metadata_length", C.c_int)]


def bind(dll, name, args, result):
    f = getattr(dll, name)
    f.argtypes = args
    f.restype = result
    return f


def copy_bgra(data, width, height, stride):
    if not data or not (1 <= width <= 8192 and 1 <= height <= 8192):
        raise ValueError("Ungültige Bildgröße oder leerer Bildpuffer")
    if stride < width * 4 or stride > width * 16:
        raise ValueError(f"Nicht unterstützte Zeilenbreite: {stride}")
    raw = np.ctypeslib.as_array((C.c_uint8 * (stride * height)).from_address(data))
    # Always copy before the native library frees/reuses its receive buffer.
    bgra = raw.reshape(height, stride)[:, :width * 4].reshape(height, width, 4)
    return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)


def field_to_picture(field, parity):
    """One NDI field (half height, BGR) as a full picture: its rows at their place, the rows in
    between the average of their neighbours (as field_picture for SDI)."""
    height = field.shape[0] * 2
    out = np.empty((height,) + field.shape[1:], field.dtype)
    out[parity::2] = field
    between = cv2.addWeighted(field[:-1], 0.5, field[1:], 0.5, 0)
    if parity == 0:
        out[1:height - 1:2] = between
        out[height - 1] = field[-1]
    else:
        out[2::2] = between
        out[0] = field[0]
    return out


class FieldPacer:
    """Interlaced network pictures (NDI 'interleaved', OMT 'Interlaced' flag, e.g. vMix in 1080i):
    both fields of one picture become two full pictures ("bob", as for SDI), the second released
    half a frame later. The receiver then delivers 50 pictures per second instead of 25 combed ones."""
    def __init__(self):
        self.pending = None

    def due(self):
        if self.pending is not None and time.perf_counter() >= self.pending[0]:
            event, self.pending = self.pending[1], None
            return event
        return None

    def wait_ms(self, timeout_ms):
        if self.pending is None:
            return timeout_ms
        return max(1, min(timeout_ms, int((self.pending[0] - time.perf_counter()) * 1000) + 1))

    def wait(self, timeout_ms):
        """While a second field waits, nothing new is received: the next frame, often already queued
        in the SDK while the first was being split, would replace it (seen on .102, where splitting
        takes ~16 ms: 30 instead of 50 pictures per second, woven SDI output jumped). Sleeps at most
        timeout_ms, so the caller's own deadlines still hold."""
        if self.pending is None:
            return None
        delay = self.pending[0] - time.perf_counter()
        if delay > timeout_ms / 1000:
            time.sleep(timeout_ms / 1000)
            return None
        if delay > 0:
            time.sleep(delay)
        event, self.pending = self.pending[1], None
        return event

    def split(self, image, arrival_ms, source_time, fps, arrived=None):
        from .sdi_input import field_picture
        rate = Fraction(fps)
        half_ms, half_ticks = round(500 / rate), round(5_000_000 / rate)
        rows = image[:image.shape[0] // 2 * 2]
        first, second = field_picture(rows, 0), field_picture(rows, 1)  # top field first, as broadcast 1080i50
        self.pending = ((time.perf_counter() if arrived is None else arrived) + half_ms / 1000,
                        MediaEvent("video", second, arrival_ms + half_ms, source_time + half_ticks, fps=rate * 2, scan='i'))
        return MediaEvent("video", first, arrival_ms, source_time, fps=rate * 2, scan='i')


class NDI:
    def __init__(self):
        candidates = [os.environ.get("FASTVIDEO_NDI_DLL", ""),
                      str(Path(os.environ.get("NDI_RUNTIME_DIR_V6", "")) / "Processing.NDI.Lib.x64.dll"),
                      r"C:\Program Files\NDI\NDI 6 Tools\Runtime\Processing.NDI.Lib.x64.dll"]
        path = next((p for p in candidates if p and Path(p).is_file()), None)
        if not path:
            raise RuntimeError("NDI is not installed. Install NDI Tools from https://ndi.video/tools/ for NDI input or output.")
        self.directory = os.add_dll_directory(str(Path(path).parent))
        self.dll = C.CDLL(path)
        self.initialize = bind(self.dll, "NDIlib_initialize", [], C.c_bool)
        self.find_create = bind(self.dll, "NDIlib_find_create_v2", [C.POINTER(NDIFindSettings)], C.c_void_p)
        self.find_destroy = bind(self.dll, "NDIlib_find_destroy", [C.c_void_p], None)
        self.find_wait = bind(self.dll, "NDIlib_find_wait_for_sources", [C.c_void_p, C.c_uint32], C.c_bool)
        self.find_sources = bind(self.dll, "NDIlib_find_get_current_sources",
                                 [C.c_void_p, C.POINTER(C.c_uint32)], C.POINTER(NDISource))
        self.recv_create = bind(self.dll, "NDIlib_recv_create_v3", [C.POINTER(NDIRecvSettings)], C.c_void_p)
        self.recv_destroy = bind(self.dll, "NDIlib_recv_destroy", [C.c_void_p], None)
        self.capture = bind(self.dll, "NDIlib_recv_capture_v2",
                            [C.c_void_p, C.POINTER(NDIVideo), C.c_void_p, C.c_void_p, C.c_uint32], C.c_int)
        self.free_video = bind(self.dll, "NDIlib_recv_free_video_v2", [C.c_void_p, C.POINTER(NDIVideo)], None)
        self.free_audio = bind(self.dll, "NDIlib_recv_free_audio_v2", [C.c_void_p, C.POINTER(NDIAudio)], None)
        if not self.initialize():
            raise RuntimeError("NDI-Initialisierung fehlgeschlagen")
        self.finder = self.find_create(C.byref(NDIFindSettings(True, None, None)))
        if not self.finder:
            raise RuntimeError("NDI-Suche konnte nicht starten")
        self.lock = threading.Lock()

    def discover(self):
        with self.lock:
            self.find_wait(self.finder, 1000)
            count = C.c_uint32()
            sources = self.find_sources(self.finder, C.byref(count))
            return [{"kind": "ndi", "name": sources[i].name.decode("utf-8", "replace"),
                     "address": (sources[i].url or b"").decode("utf-8", "replace")}
                    for i in range(count.value)]

    def receiver(self, source):
        # color_format=0 requests BGRX/BGRA; bandwidth=100 is highest.
        config = NDIRecvSettings(NDISource(source["name"].encode(), source["address"].encode()),
                                 0, 100, True, b"FastVideo Bridge")
        handle = self.recv_create(C.byref(config))
        if not handle:
            raise RuntimeError("NDI-Empfänger konnte nicht starten")
        return NDIReceiver(self, handle)

    def close(self):
        self.find_destroy(self.finder)


class NDIReceiver:
    def __init__(self, library, handle):
        self.lib, self.handle = library, handle
        self.fields = FieldPacer()

    def read(self):
        item = self.read_event()
        return (item.data, item.capture_ms) if item and item.kind == "video" else None

    def read_event(self,timeout_ms=20):
        if self.fields.pending is not None:
            return self.fields.wait(timeout_ms)
        frame = NDIVideo()
        audio = NDIAudio()
        result = self.lib.capture(self.handle, C.byref(frame), C.byref(audio), None, self.fields.wait_ms(timeout_ms))
        received = time.perf_counter()
        arrival_ms = time.time_ns() // 1_000_000
        if result == 2:
            try:
                return MediaEvent("audio", copy_audio(audio.data, audio.samples, audio.channels, audio.stride),
                                  arrival_ms, audio.timecode, audio.sample_rate)
            finally:
                self.lib.free_audio(self.handle, C.byref(audio))
        if result != 1:
            if result == 4:
                raise RuntimeError("NDI-Empfang meldet einen Fehler")
            return None
        try:
            fourcc = frame.fourcc.to_bytes(4, "little")
            if fourcc not in (b"BGRA", b"BGRX"):
                raise RuntimeError(f"Unerwartetes NDI-Pixelformat: {fourcc!r}")
            image = copy_bgra(frame.data, frame.width, frame.height, frame.stride)
            fps = Fraction(frame.fps_n,frame.fps_d) if frame.fps_d else 0
            # NDI frame_format: 1 progressive, 0 interleaved (both fields in one picture).
            if frame.frame_format == 0 and fps and KEEP_INTERLACED:
                return MediaEvent("video", image, arrival_ms, frame.timecode, fps=fps, scan='woven')
            if frame.frame_format == 0 and fps:
                return self.fields.split(image, arrival_ms, frame.timecode, fps, received)
            if frame.frame_format in (2, 3) and fps:
                # Single fields (field 0 / field 1 from some senders): each becomes a full picture.
                rate = fps * 2 if fps <= 30 else fps
                return MediaEvent("video", field_to_picture(image, frame.frame_format - 2), arrival_ms, frame.timecode, fps=rate, scan='i')
            return MediaEvent("video", image, arrival_ms, frame.timecode, fps=fps)
        finally:
            self.lib.free_video(self.handle, C.byref(frame))

    def close(self):
        self.lib.recv_destroy(self.handle)


class OMT:
    def __init__(self):
        folder = Path(os.environ.get("FASTVIDEO_OMT_DIR", str(ROOT / "vendor" / "omt")))
        self.directory = os.add_dll_directory(str(folder))
        # Avoid modifying the settings of other OMT applications on this PC.
        os.environ["OMT_STORAGE_PATH"] = str(ROOT / "work" / "omt-state")
        # .NET NativeAOT uses its own P/Invoke loader, which does not honor
        # Python's AddDllDirectory in every configuration. Preload by full path.
        self.vmx = C.CDLL(str(folder / "libvmx.dll"))
        self.dll = C.CDLL(str(folder / "libomt.dll"))
        self.addresses = bind(self.dll, "omt_discovery_getaddresses", [C.POINTER(C.c_int)], C.POINTER(C.c_char_p))
        self.create = bind(self.dll, "omt_receive_create", [C.c_char_p, C.c_int, C.c_int, C.c_int], C.c_void_p)
        self.receive = bind(self.dll, "omt_receive", [C.c_void_p, C.c_int, C.c_int], C.POINTER(OMTFrame))
        self.destroy = bind(self.dll, "omt_receive_destroy", [C.c_void_p], None)
        self.shutdown = bind(self.dll, "omt_shutdown", [], None)
        logging = bind(self.dll, "omt_setloggingfilename", [C.c_char_p], None)
        # libomt aborts the whole process (unhandled .NET exception) if the folder is missing.
        (ROOT / "work").mkdir(exist_ok=True)
        logging(str(ROOT / "work" / f"omt-{os.getpid()}.log").encode())
        self.lock = threading.Lock()
        # First access starts asynchronous DNS-SD discovery, so start early.
        count = C.c_int()
        self.addresses(C.byref(count))

    def discover(self):
        with self.lock:
            count = C.c_int()
            addresses = self.addresses(C.byref(count))
            return [{"kind": "omt", "name": addresses[i].decode("utf-8", "replace"),
                     "address": addresses[i].decode("utf-8", "replace")} for i in range(count.value)]

    def receiver(self, source):
        # Video and program audio from the same connection, BGRA output.
        handle = self.create(source["address"].encode(), 6, 2, 0)
        if not handle:
            raise RuntimeError("OMT-Empfänger konnte nicht starten")
        return OMTReceiver(self, handle)

    def close(self):
        self.shutdown()


class OMTReceiver:
    def __init__(self, library, handle):
        self.lib, self.handle = library, handle
        self.timings = Timings()
        self.fields = FieldPacer()

    def read(self):
        item = self.read_event()
        return (item.data, item.capture_ms) if item and item.kind == "video" else None

    def read_event(self,timeout_ms=20):
        if self.fields.pending is not None:
            return self.fields.wait(timeout_ms)
        sdk_started = time.perf_counter()
        ptr = self.lib.receive(self.handle, 6, self.fields.wait_ms(timeout_ms))
        sdk_returned = time.perf_counter()
        arrival_ms = time.time_ns() // 1_000_000
        if not ptr:
            self.timings.add("sdk_empty_wait", sdk_started)
            return None
        frame = ptr.contents
        self.timings.add("sdk_video_wait" if frame.type == 2 else "sdk_audio_wait", sdk_started)
        fourcc = frame.codec.to_bytes(4, "little")
        if frame.type == 4:
            if fourcc != b"FPA1" or frame.data_length < frame.samples * frame.channels * 4:
                raise RuntimeError("Nicht unterstütztes OMT-Audioformat")
            audio = copy_audio(frame.data, frame.samples, frame.channels, frame.samples*4)
            self.timings.add("audio_copy", sdk_returned)
            return MediaEvent("audio", audio,
                              arrival_ms, frame.timestamp, frame.sample_rate)
        if frame.type != 2 or fourcc not in (b"BGRA", b"BGRX"):
            raise RuntimeError(f"Unerwartetes OMT-Pixelformat: {fourcc!r}")
        if frame.data_length < frame.stride * frame.height:
            raise RuntimeError("OMT-Bildpuffer ist zu klein")
        image = copy_bgra(frame.data, frame.width, frame.height, frame.stride)
        self.timings.add("video_copy_bgra_bgr", sdk_returned)
        fps = Fraction(frame.fps_n,frame.fps_d) if frame.fps_d else 0
        if frame.flags & OMT_INTERLACED and fps and KEEP_INTERLACED:
            return MediaEvent("video", image, arrival_ms, frame.timestamp, fps=fps, scan='woven')
        if frame.flags & OMT_INTERLACED and fps:
            started = time.perf_counter()
            event = self.fields.split(image, arrival_ms, frame.timestamp, fps, sdk_returned)
            self.timings.add("video_deinterlace", started)
            return event
        return MediaEvent("video", image, arrival_ms, frame.timestamp, fps=fps)

    def close(self):
        self.lib.destroy(self.handle)


class TestReceiver:
    def __init__(self):
        self.tick = 0
        self.deadline = time.monotonic()
        self.background=np.zeros((720,1280,3),np.uint8)
        colors=[(190,190,190),(0,190,190),(190,190,0),(0,190,0),
                (190,0,190),(0,0,190),(190,0,0),(30,30,30)]
        for i,color in enumerate(colors):self.background[:,i*160:(i+1)*160]=color
        cv2.rectangle(self.background,(0,560),(1280,720),(24,24,24),-1)
        cv2.rectangle(self.background,(400,100),(880,480),(0,0,0),-1)
        logo=cv2.imread(str(ROOT/'static'/'Logo_fastvideo.png'),cv2.IMREAD_UNCHANGED)
        if logo is not None:
            width=320;height=round(logo.shape[0]*width/logo.shape[1])
            logo=cv2.resize(logo,(width,height),interpolation=cv2.INTER_AREA)
            target=self.background[155:155+height,480:480+width]
            if logo.shape[2]==4:
                alpha=logo[:,:,3:4].astype(np.float32)/255
                target[:]=(logo[:,:,:3]*alpha+target*(1-alpha)).astype(np.uint8)
            else:target[:]=logo[:,:,:3]
        self.date_key=None

    def read(self):
        item = self.read_event()
        return (item.data,item.capture_ms) if item.kind == "video" else None

    def read_event(self,timeout_ms=20):
        if not hasattr(self,"sample_cursor"):
            self.sample_cursor = 0
            self.event_origin = time.monotonic()
            self.pending = None
        if self.pending is not None:
            item,self.pending = self.pending,None
            return item
        cursor = self.sample_cursor
        time.sleep(max(0,self.event_origin+cursor/48000-time.monotonic()))
        timestamp = time.time_ns()//1_000_000
        source_time = cursor*10_000_000//48000
        phase = (cursor+np.arange(960))%96000
        beep = np.where(phase<1920,.25*np.sin(2*np.pi*1000*phase/48000),0).astype(np.float32)
        audio = MediaEvent("audio",np.stack([beep,beep]),timestamp,source_time,48000)
        self.sample_cursor += 960
        if cursor%1920:
            return audio
        self.pending = audio
        now=datetime.fromtimestamp(timestamp/1000).astimezone()
        if self.date_key!=now.date():
            self.date_key=now.date()
            weekdays=('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')
            months=('January','February','March','April','May','June','July','August','September','October','November','December')
            date=f'{weekdays[now.weekday()]}, {now.day} {months[now.month-1]} {now.year}'
            self.background[304:342,418:863]=(0,0,0)
            date_width=cv2.getTextSize(date,cv2.FONT_HERSHEY_SIMPLEX,.72,1)[0][0]
            cv2.putText(self.background,date,(640-date_width//2,332),cv2.FONT_HERSHEY_SIMPLEX,.72,(255,255,255),1,cv2.LINE_AA)
        image=self.background.copy()
        x = (self.tick * 12) % 1280
        cv2.rectangle(image, (x, 200), (min(x + 60, 1279), 550), (255, 255, 255), -1)
        cv2.rectangle(image, (0, 560), (1280, 720), (24, 24, 24), -1)
        cv2.putText(image, "FastVideo - INTERNAL TEST", (40, 610), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
        cv2.putText(image, f"Sender UTC ms: {timestamp} / Frame {self.tick}", (40, 660),
                    cv2.FONT_HERSHEY_SIMPLEX, .8, (210, 210, 210), 2)
        flash = cursor%96000 < 1920
        cv2.rectangle(image,(400,100),(880,480),(255,255,255) if flash else (0,0,0),-1)
        cv2.putText(image,"FLASH + CLICK / 2 s",(410,530),cv2.FONT_HERSHEY_SIMPLEX,.75,(255,255,255),2)
        image[118:463,418:863]=self.background[118:463,418:863]
        clock=f'{now.hour:02}:{now.minute:02}:{now.second:02}.{now.microsecond//10000:02}'
        clock_width=cv2.getTextSize(clock,cv2.FONT_HERSHEY_SIMPLEX,1.3,2)[0][0]
        cv2.putText(image,clock,(640-clock_width//2,397),cv2.FONT_HERSHEY_SIMPLEX,1.3,(255,255,255),2,cv2.LINE_AA)
        self.tick += 1
        return MediaEvent("video",image,timestamp,source_time,fps=25)

    def close(self):
        pass


class Sources:
    def __init__(self):
        self.libraries = {}
        self.errors = {}
        from .sdi_input import SDI
        for kind, cls in (("ndi", NDI), ("omt", OMT), ("sdi", SDI)):
            try:
                self.libraries[kind] = cls()
            except Exception as exc:
                self.errors[kind] = str(exc)

    def discover(self):
        result = [{"kind": "test", "name": "Internes Testbild (keine Netzwerkquelle)", "address": "test"}]
        for kind, library in self.libraries.items():
            try:
                result.extend(library.discover())
            except Exception as exc:
                self.errors[kind] = str(exc)
        return {"sources": result, "errors": self.errors}

    def receiver(self, source):
        if source["kind"] == "test":
            return TestReceiver()
        return self.libraries[source["kind"]].receiver(source)

    def close(self):
        for library in self.libraries.values():
            library.close()
