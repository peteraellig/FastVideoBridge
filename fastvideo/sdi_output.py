"""Decoded video/stereo PCM -> scheduled Blackmagic SDI, one hardware timeline."""
import ctypes as C
from collections import deque
from fractions import Fraction
import threading
import time
import av
import numpy as np
from .decklink import Callback,P,I,U,L,select,modes
from .diagnostics import Timings

PROGRESSIVE,UPPER_FIELD_FIRST=int.from_bytes(b'prog','big'),int.from_bytes(b'uppr','big')
# Interlaced output: 1080 pictures at a field rate are woven in pairs into one interlaced frame;
# at a frame rate (25p) both fields come from the same picture.
FIELD_RATES={Fraction(50),Fraction(60000,1001),Fraction(60)}
FRAME_RATES={Fraction(25),Fraction(30000,1001),Fraction(30)}

def uyvy_rows(frame):
    plane=frame.planes[0]
    return np.frombuffer(plane,np.uint8).reshape(frame.height,plane.line_size)[:,:frame.width*2]

def field_parity(rows):
    """+1 when the odd rows are the average of their neighbours (a picture bobbed from the top
    field, as the sending Bridge deinterlaces 1080i), -1 for the bottom field, about 0 for
    progressive pictures. Luma of every 8th pixel of UYVY rows, well under a millisecond."""
    y=rows[:,1::16].astype(np.int16)
    odd=np.abs(2*y[1:-1:2]-y[0:-2:2]-y[2::2]).mean()
    even=np.abs(2*y[2:-1:2]-y[1:-2:2]-y[3::2]).mean()
    return float((even-odd)/(even+odd+1e-6))

def format_name(width,height,rate,dominance):
    return f'{width}x{height}-{float(rate*2 if dominance!=PROGRESSIVE else rate):g}{"p" if dominance==PROGRESSIVE else "i"}'

class SdiSender:
    def __init__(self,index,interlaced=False):
        self.output,self.address=select(index,'IDeckLinkOutput_v14_2_1')
        self.interlaced=interlaced;self.held=None;self.phase_score=0.0;self.phase_pairs=0;self.phase_shifts=0
        self.device_index=index;self.available=modes(self.output);self.timings=Timings()
        self.lock=threading.RLock();self.mode=None;self.origin=None;self.first_display=None;self.playing=False;self.pending_audio=deque(maxlen=10)
        self.completion={'completed':0,'late':0,'dropped':0,'flushed':0};self.audio_drops=0;self.video_drops=0
        self.schedule_lead_ms=None;self.buffered_video=0;self.resyncs=0;self.trims=0;self.pads=0
        self.last_timestamp=None;self.last_display=None;self.audio_end=None;self.lead_min=None;self.lead_frames=0;self.lead_low=0
        self.resampler=av.AudioResampler(format='s32',layout='stereo',rate=48000)
        self.reformatter=av.video.reformatter.VideoReformatter()
        self.callback=Callback('IDeckLinkVideoOutputCallback_v14_2_1',{'ScheduledFrameCompleted':self._completed,'ScheduledPlaybackHasStopped':lambda:None})
        self.output.call('SetScheduledFrameCompletionCallback',self.callback.pointer)
    def _completed(self,frame,result):
        key={0:'completed',1:'late',2:'dropped',3:'flushed'}.get(result,'dropped');self.completion[key]+=1
    def _disable(self):
        if self.mode:
            actual=L()
            if self.playing:self.output.call('StopScheduledPlayback',0,C.byref(actual),10000000)
            self.output.call('DisableAudioOutput');self.output.call('DisableVideoOutput')
        self.mode=None;self.origin=None;self.first_display=None;self.playing=False;self.pending_audio.clear()
        self.last_timestamp=None;self.last_display=None;self.audio_end=None;self.lead_min=None;self.lead_frames=0;self.lead_low=0
    def _display(self,timestamp):
        # Advance from the previously scheduled slot. Rounding each timestamp
        # independently collides/skips slots when the sender's timeline sits
        # near half a frame from the card grid (e.g. after a source restart).
        duration=self.mode['fps_d'];scale=self.mode['fps_n']
        if self.last_timestamp is None:
            return round((timestamp-self.origin)*scale/(10000000*duration))*duration
        return self.last_display+round((timestamp-self.last_timestamp)*scale/(10000000*duration))*duration
    def _interlace(self,rows,timestamp,fps):
        """(top rows, bottom rows, timestamp, frame rate, field dominance) for the card, or None
        while the first picture of a pair waits for the second."""
        rate=Fraction(fps).limit_denominator(1001)
        if not self.interlaced or rows.shape[0]!=1080 or rate not in FIELD_RATES|FRAME_RATES:
            self.held=None;return rows,rows,timestamp,rate,PROGRESSIVE
        if rate in FRAME_RATES:
            self.held=None;return rows,rows,timestamp,rate,UPPER_FIELD_FIRST
        parity=field_parity(rows)
        if self.held is None or self.held[3]!=rate:
            self.held=(rows,timestamp,parity,rate);return None
        first,first_time,first_parity,_=self.held;self.held=None
        # A bobbed 1080i source sends top-field picture, bottom-field picture. A pairing that
        # starts on a bottom field is moved by one picture (normally once, at start). For
        # progressive pictures about half the pairs vote either way and the pairing is left alone.
        # A vote, not a mean: after H.264 at 1 Mbit/s the measure shrinks to about ±0.15, but its
        # sign still alternates correctly for 99 % of live vMix 1080i pictures.
        self.phase_score+=first_parity<parity;self.phase_pairs+=1
        if self.phase_pairs>=25:
            wrong=self.phase_score>=20
            self.phase_score=0.0;self.phase_pairs=0
            if wrong:
                self.phase_shifts+=1
                self.held=(rows,timestamp,parity,rate);return None
        return first,rows,first_time,rate/2,UPPER_FIELD_FIRST
    def _configure(self,frame,fps,dominance=PROGRESSIVE):
        rate=Fraction(fps).limit_denominator(1001)
        mode=next((m for m in self.available if (m['width'],m['height'])==(frame.width,frame.height) and m['field_dominance']==dominance and Fraction(m['fps_n'],m['fps_d'])==rate),None)
        if not mode:raise ValueError(f'SDI connector does not support {format_name(frame.width,frame.height,rate,dominance)}. Select 720p output or another source format.')
        actual=I();supported=I()
        self.output.call('DoesSupportVideoMode',1,mode['id'],846624121,1852796517,0,C.byref(actual),C.byref(supported))
        if not supported.value:raise ValueError('Selected SDI connector does not support the received video format')
        old_audio=list(self.pending_audio)
        self._disable()
        self.output.call('EnableVideoOutput',mode['id'],0)
        self.mode=mode
        try:
            self.output.call('EnableAudioOutput',48000,32,2,2)
            self.output.call('BeginAudioPreroll')
        except Exception:self._disable();raise
        self.pending_audio.extend(old_audio)
    def video(self,frame,timestamp,fps):
        started=time.perf_counter()
        # The serial video worker owns the reformatter and this decoded frame.
        # Colour conversion does not touch the card or its shared timeline;
        # audio must be able to schedule while this CPU work is running.
        stage=time.perf_counter()
        converted=self.reformatter.reformat(frame,format='uyvy422')
        self.timings.add('video_sdi_colour',stage)
        top=bottom=None;dominance=PROGRESSIVE
        if getattr(self,'interlaced',False):
            picture=self._interlace(uyvy_rows(converted),timestamp,fps)
            if picture is None:return
            top,bottom,timestamp,fps,dominance=picture
        waiting=time.perf_counter()
        with self.lock:
            self.timings.add('video_sdi_lock_wait',waiting)
            if not self.output:return
            if self.mode is None or (self.mode['width'],self.mode['height'],Fraction(self.mode['fps_n'],self.mode['fps_d']),self.mode.get('field_dominance',PROGRESSIVE))!=(frame.width,frame.height,Fraction(fps),dominance):
                self._configure(frame,fps,dominance)
            if self.origin is None:
                # Start hardware time at the first picture. Earlier startup audio
                # is trimmed rather than leaving an empty future video interval.
                self.origin=timestamp
            lead=self._lead(timestamp)
            if lead is not None and (lead>7*self.mode['fps_d'] or lead<-self.mode['fps_n']//4):
                # The sender restarted its timeline within the same WebRTC session.
                # Re-anchor instead of dropping against an unreachable schedule.
                self.resyncs+=1
                self._configure(frame,fps,dominance)
                self.pending_audio.clear()
                self.origin=timestamp
                lead=None
            action=self._lead_action(lead) if lead is not None else None
            if action=='trim':
                # Startup latency fixed a larger lead than the settled pipeline
                # needs, or the card's clock runs slower than the source. Leave out
                # one picture and the same 20 ms of audio so both move closer to air
                # while staying in sync.
                self.trims+=1
                self.last_display=self._display(timestamp)-self.mode['fps_d']
                self.last_timestamp=timestamp
                self.origin+=round(self.mode['fps_d']*10000000/self.mode['fps_n'])
                return
            if action=='pad':
                # The card's clock runs faster than the source: the lead is used up and every
                # picture would arrive too late. Move the schedule one picture later; the card
                # repeats one picture and the sound gets a pause of the same length, in sync.
                self.pads+=1
                self.last_display+=self.mode['fps_d']
                self.origin-=round(self.mode['fps_d']*10000000/self.mode['fps_n'])
            count=U();self.output.call('GetBufferedVideoFrameCount',C.byref(count))
            self.buffered_video=count.value
            if count.value>=6:self.video_drops+=1;return
            stage=time.perf_counter()
            native=self.output.interface('CreateVideoFrame','IDeckLinkMutableVideoFrame_v14_2_1',frame.width,frame.height,frame.width*2,846624121,0)
            self.timings.add('video_sdi_allocate',stage)
            try:
                stage=time.perf_counter()
                pointer=P();native.call('GetBytes',C.byref(pointer))
                plane=converted.planes[0];stride=frame.width*2
                if top is not bottom:
                    # Weave: even rows (upper field) from the first picture, odd rows from the second.
                    card=np.ctypeslib.as_array((C.c_uint8*(stride*frame.height)).from_address(pointer.value)).reshape(frame.height,stride)
                    card[0::2]=top[0::2];card[1::2]=bottom[1::2]
                elif plane.line_size==stride:C.memmove(pointer,plane.buffer_ptr,stride*frame.height)
                else:
                    for row in range(frame.height):C.memmove(pointer.value+row*stride,plane.buffer_ptr+row*plane.line_size,stride)
                self.timings.add('video_sdi_copy',stage)
                duration=self.mode['fps_d'];scale=self.mode['fps_n']
                display=self._display(timestamp)
                if display<0 or (self.last_display is not None and display<=self.last_display):self.video_drops+=1;return
                if self.playing:
                    current=L();speed=C.c_double()
                    self.output.call('GetScheduledStreamTime',scale,C.byref(current),C.byref(speed))
                    self.schedule_lead_ms=round((display-current.value)*1000/scale,2)
                stage=time.perf_counter()
                self.output.call('ScheduleVideoFrame',native.pointer,display,duration,scale)
                self.timings.add('video_sdi_driver_schedule',stage)
                self.last_timestamp,self.last_display=timestamp,display
                if self.first_display is None:self.first_display=display
            finally:native.close()
            while self.pending_audio:
                stamp,audio=self.pending_audio.popleft();self._audio(audio,stamp)
            self._start_ready()
        self.timings.add('video_sdi_schedule',started)
    def _lead(self,timestamp):
        # Distance between this picture's slot and the card's playback clock,
        # in display units. The card holds at most 6 frames.
        if not self.playing or self.last_display is None:return None
        current=L();speed=C.c_double()
        self.output.call('GetScheduledStreamTime',self.mode['fps_n'],C.byref(current),C.byref(speed))
        return self._display(timestamp)-current.value
    def _lead_action(self,lead):
        """Keep the lead between one and three pictures. Source and card run on separate clocks
        (20 ppm apart is one picture in about 17 minutes). Judged over 50 pictures: 'trim' when
        even the smallest lead was above three pictures, 'pad' when at least five pictures had
        less than one picture of lead (single late arrivals do not count), else None."""
        self.lead_min=lead if self.lead_min is None else min(self.lead_min,lead)
        self.lead_frames+=1
        if lead<self.mode['fps_d']:self.lead_low+=1
        if self.lead_frames<50:return None
        action='trim' if self.lead_min>3*self.mode['fps_d'] else 'pad' if self.lead_low>=5 else None
        self.lead_min=None;self.lead_frames=0;self.lead_low=0
        return action
    def _start_ready(self):
        # Video and audio arrive on separate workers. Do not end audio preroll
        # merely because three pictures arrived first: the driver needs both
        # queues prepared before starting their common hardware clock.
        if self.playing or self.first_display is None:return
        video=U();audio=U()
        self.output.call('GetBufferedVideoFrameCount',C.byref(video))
        self.output.call('GetBufferedAudioSampleFrameCount',C.byref(audio))
        if video.value<3 or audio.value==0:return
        self.output.call('EndAudioPreroll')
        # Pictures keep arriving while preroll waits for audio. Starting at the
        # first picture would leave a lead larger than the 6-frame buffer for
        # the whole session; start two frames behind the newest one instead.
        start=max(self.first_display,self.last_display-2*self.mode['fps_d'])
        self.output.call('StartScheduledPlayback',start,self.mode['fps_n'],1.0)
        self.playing=True
    def _audio(self,audio,timestamp):
        samples=audio.shape[0]//2;relative=round((timestamp-self.origin)*48000/10000000)
        if relative<0:
            trim=min(samples,-relative);audio=audio[trim*2:];samples-=trim;relative=0
        if self.audio_end is not None:
            # Join packets seamlessly; after a trim, cut what overlaps the
            # audio that is already buffered on the card.
            if abs(relative-self.audio_end)<=2:relative=self.audio_end
            elif relative<self.audio_end:
                trim=min(samples,self.audio_end-relative);audio=audio[trim*2:];samples-=trim;relative+=trim
        if not samples:return
        buffered=U();self.output.call('GetBufferedAudioSampleFrameCount',C.byref(buffered))
        # At most about 5 pictures of audio ahead: 100 ms at 50p, 200 ms for 25 frames (1080i50 woven),
        # whose video also runs that far ahead (fixed 100 ms dropped 13 packets/s on .102).
        limit=max(4800,round(48000*5*self.mode['fps_d']/self.mode['fps_n'])) if self.mode else 4800
        if buffered.value>limit:self.audio_drops+=1;return
        audio=np.ascontiguousarray(audio)
        written=U();self.output.call('ScheduleAudioSamples',P(audio.ctypes.data),samples,relative,48000,C.byref(written))
        if written.value<samples:self.audio_drops+=1
        self.audio_end=relative+written.value
    def audio(self,frame,timestamp):
        started=time.perf_counter()
        with self.lock:
            self.timings.add('audio_sdi_lock_wait',started)
            if not self.output:return
            offset=0
            for converted in self.resampler.resample(frame):
                audio=converted.to_ndarray().reshape(-1).copy()
                stamp=timestamp+round(offset*10000000/48000);offset+=converted.samples
                if self.origin is None:
                    if len(self.pending_audio)==self.pending_audio.maxlen:self.audio_drops+=1
                    self.pending_audio.append((stamp,audio))
                else:self._audio(audio,stamp)
            self._start_ready()
        self.timings.add('audio_sdi_schedule',started)
    def reset_stream(self):
        with self.lock:
            if self.output:
                self._disable();self.held=None;self.phase_score=0.0;self.phase_pairs=0
                self.resampler=av.AudioResampler(format='s32',layout='stereo',rate=48000)
    def snapshot(self):return dict(device=self.address,playing=self.playing,mode=self.mode,completion=dict(self.completion),video_drops=self.video_drops,audio_drops=self.audio_drops,schedule_lead_ms=self.schedule_lead_ms,buffered_video=self.buffered_video,resyncs=self.resyncs,lead_trims=self.trims,lead_pads=self.pads,interlaced=self.interlaced,field_phase_shifts=self.phase_shifts)
    def close(self):
        with self.lock:
            if not self.output:return
            try:self._disable();self.output.call('SetScheduledFrameCompletionCallback',None)
            finally:self.output.close();self.output=None
