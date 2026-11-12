# FastVideo Bridge · 0.0.5.14

Standalone Windows sender and receiver for OMT / NDI / Blackmagic SDI, WHIP, WHEP and SRT output.
The FastVideo project is separate and unchanged. Source, git history and everything needed to
build the installer are in Z:\FastvideoBridge (NAS). Installed copies live in C:\FastVideoBridge.

## Receive a FastVideo link (WHEP)

In the receive card, choose **FastVideo link / WHEP**, paste the viewer link,
select OMT, NDI or Blackmagic SDI output, then click **Connect**. Personal links are supported;
each Bridge connection occupies one of that user's allowed connections.
Auto prefers H264 for broad hardware-decoder support. AV1 can be selected
explicitly, or requested with `?codec=av1` in the viewer link. Decoding uses the
existing hardware path when supported and otherwise falls back to software.
The card reports the actual decoder, input/output format, frame counts and drops.

The receiving Bridge makes an outgoing connection like a browser and requires
no router port forwarding. The FastVideo server's existing WebRTC UDP media port
must remain reachable. WHIP input is a separate mode with its own receive port.
No changes to the original FastVideo server are required.

Temporary connection failures retry automatically. An invalid or occupied
personal link, or a disconnect requested by the organiser, stops automatic retries
and displays the reason. Stop and reconnect when permitted. Closing a session
also releases its server-side resource. Viewer links and bearer tokens are saved
encrypted on this Windows machine, separately from WHIP input credentials.

## Start and stop

Double-click Start-Bridge.bat to start the background service and open
http://127.0.0.1:18879/sender. Stop-Bridge.bat stops both outputs and the service.
The BAT launchers delegate to hidden PowerShell helper in scripts. A command window may appear briefly when launching a BAT file.

The installer bundles its own Python runtime and media gateway; no FastVideo
installation is required. Code, source selection, settings and logs are separate.
HTTP control: 18879; Go/Pion: 18880; internal media ports are selected automatically
from the configured pair (default 20999/21000) to avoid occupied ports.
The additional feed is loopback-only; it needs no router or firewall port opening.

## Input and outputs

Choose OMT, NDI or Blackmagic SDI and click Set source. SDI2OMT can also remain an external source.
Both output Start buttons also start the confirmed input if it is stopped.
Source audio channels 1 and 2 are kept as left and right. WHIP uses Opus;
SRT uses one stereo AAC track at 48 kHz in MPEG-TS. Audio bitrate is selectable
from 128/192/256/320/384/512 kbit/s, default 256 kbit/s total for both channels.
WHIP Opus offers 64/96/128/160/192/256/320 kbit/s, default 256 kbit/s total.
Both audio selections are saved with the output forms and applied on Start.
Changing Opus replaces only its encoder at a frame boundary, preserving source
timestamps; SRT PCM/AAC, input and video encoders are unaffected.

The compact WHIP and SRT cards show connection settings above matching codec and
bitrate/quality controls. Quality is chosen independently in each output card
and applied when that output starts. Both offer 1–10 Mbit/s in 0.5 Mbit/s steps.
Input occupies the left half of the upper row; Diagnostics occupies the right.
Numeric diagnostics use fixed widths, right alignment and tabular monospace
digits. The dashboard fits a 1366 × 768 viewport without scrolling when the
manual source address is collapsed. The Bridge logo is 220 px wide and the
green title is centered independently of the logo and clock.
WHIP: endpoint, stream key, AV1, H264 or H265. H265 uses an on-demand Intel QSV
encoder and RFC 7798 packets in the existing bounded Go media bridge. It retains
the input timeline and shared Opus audio, handles PLI with IDR requests, repeats
VPS/SPS/PPS at random-access pictures, and restarts after source replacement.
An SDP answer that rejects the selected video codec is reported as an error.
SRT: H264 or H265, caller only, destination IP/hostname, port, latency in
milliseconds, stream ID, passphrase and AES 128/192/256. AV1 was removed from
SRT at the user's request; WHIP continues to offer AV1.
An empty passphrase means unencrypted SRT. A passphrase must contain 10–79 bytes.
AES is used only with a passphrase. IPv4/hostnames are supported in this initial UI.
SRT also offers a keyframe interval of 0.5–10 seconds (0.5-second steps, default 1)
and a CBR checkbox (default off). CBR selects the Intel constant-video-bitrate
control mode; AAC and container/SRT overhead add traffic beyond the video target.
SRT always owns a separate QSV encoder without changing WHIP.

Each output can be started and stopped independently and retries after failure.
Stop input pauses the media pipeline; enabled outputs wait and reconnect when input
returns. Stop an output explicitly to stop its retries. Successful Start operations
save the output forms in user/bridge-outputs.json. Tokens and passphrases use Windows
DPAPI encryption tied to the Windows account/machine. A local-only, no-cache endpoint
restores the forms when the page opens. Status and logs do not expose credentials.
Outputs are started manually after a service restart. Older, unsaved credentials
must be entered once again.

## Current transport and quality

SRT transport is datarhei/gosrt v0.12.0, compiled into the Go gateway. There is
no FFmpeg executable/process in the sender path. PyAV's native media libraries
package the existing H264 stream and encode AAC in a bounded background worker.
GPU initialization and encoding now run in separate hidden Python workers.
NV12 frames use one bounded shared-memory slot per encoder; private pipes carry
metadata and encoded results. Audio and video retain one source timeline.
WHIP bitrate/quality changes warm a replacement worker while the old one sends,
then swap only that publisher under its encoder lock. Capture and SRT stay in place.
HEVC SDP advertises level 4 (120), matching the 720p50 encoder SPS.

Intel H264 low_delay_brc is disabled in this copy to let periodic keyframes use
more bits instead of squeezing them to near-P-frame sizes. No B frames or lookahead
were added. The bitrate target remains 4 Mbit/s. H264 keyframes now occur every
1 s (50 frames at 50 fps), at the user's request. AV1 retains its 2 s interval.
The user's first remote SRT observation after this change was substantially better;
the original FastVideo pipeline had also been stopped, so those effects were not
isolated. End-to-end latency and long-duration WAN resilience remain unmeasured.

The vendored GoSRT ACKACK compatibility adjustment is documented in
gateway/SRT-VENDOR-PATCH.md. It removes invalid-control-packet errors seen with a
libsrt reference receiver. Reapply/retest it after regenerating vendor.

## Tests on 7 October 2026

- Go tests: existing WHIP authentication, reconnect and cleanup plus SRT caller,
  exact stream ID and payload, no encryption and AES 128/192/256.
- 20 Python tests: SRT codec/bitrate/keyframe/CBR validation, native QSV CBR mode
  and GOP confirmation, HEVC RTP fragmentation/decoding and source/user controls.
- WHIP negotiation, RTP delivery, reconnect and cleanup tested for H264/H265/AV1.
- Actual Bridge H265 WHIP -> Broadcast Box -> WHEP `kamera`: 151 received and
  decoded 1280×720 pictures, Opus audio received, no malformed RTP payloads.
  The WHIP publishing token is distinct from this receiver's playback key.
- Reference SRT receiver decoded H264/H265 with CBR and a two-second GOP,
  stereo AAC, with WHIP H265 running concurrently.
- Local reference receiver decoded shared H264 at 4 Mbit/s, independent H264 at
  1.5 Mbit/s, and Intel QSV H265 at 4 Mbit/s, all 720p50 with stereo AAC.
- Local reference receiver decoded 720p50 H264 and stereo AAC, 200 video frames
  per test, without encryption and with each AES key length.
- After the ACKACK adjustment, separate reference tests passed all four modes
  without invalid-control-packet errors.
- Real OMT -> SRT caller at 192.168.1.102:56201, no stream ID/passphrase,
  latency 120 ms. WHIP concurrently connected at 192.168.1.102:8050/api/whip.
  Around 50 fps; no reported queue/transport drops or SRT retransmissions at
  the recorded sample. This is an initial LAN result, not a long-duration guarantee.

FFmpeg/ffprobe are retained under work/test-tools solely as hidden reference test
receivers/decoders. They are not needed by the Bridge application. Runtime settings
are in fastvideo.settings.json; source selection and test reports are in work.

## Regression checks on 8 October 2026

- 22 Python checks passed: isolated QSV startup responsiveness, H264/H265/AV1
  decoding, worker cleanup, source/user controls and encrypted credential roundtrip.
- All Go checks passed, including the HEVC SDP level regression.
- Real H265 WHIP -> Broadcast Box -> WHEP: 150 pictures, 151 Opus packets,
  no malformed RTP. Browser playback compatibility remains a separate check.
- Concurrent H265 SRT plus WHIP codec/bitrate/quality changes: no source recovery,
  no queue drops, one SRT connection; largest video timestamp gap approximately
  40 ms. Earlier 32-second capture: 1,599 decoded video pictures.
- AV1 SRT capture: 270 pictures decoded with libdav1d from the MPEG-TS private
  data stream; AAC stereo present. Automatic receiver detection is not claimed.
- Reports: work/isolated-concurrent-report.json and work/srt-av1-experimental-report.json.

## 0.0.2.1: portable defaults and WHIP receive

Clean installations use H264 with automatic NVIDIA NVENC / Intel QSV / CPU selection,
not Intel AV1 as a mandatory default. Internal media ports default to 20999/21000;
the supervisor chooses and persists another available pair if either port is occupied.
Port 20000 is avoided. Existing user settings are preserved on upgrade.

The independent WHIP receive container accepts one publisher authenticated with the
configured Bearer key at http://LAN-IP:18879/api/whip-input. Start receiver, copy the
displayed URL, and enter the same key in the sending application. Decoded H264 or AV1
video and Opus stereo audio become the configured OMT source; NDI is not needed.
H265 reception is not included in this first receiver. Decoding currently uses CPU
codecs; Intel AV1 encoding hardware is not required for reception. Sender RTCP reports
align video and audio OMT timestamps. Until both reports arrive the status shows
Waiting for sender clocks; initial arrival-based timing is provisional.

WHIP supports full gathered ICE offers, 201 SDP answers, authenticated session DELETE,
and one concurrent publisher. Trickle ICE PATCH is supported; ICE restart requires reconnect.
No router forwarding is needed on the same LAN; Windows firewall must allow the app's
HTTP and WebRTC traffic. LAN HTTP key entry is intended for trusted local networks.
The receive key is encrypted with Windows DPAPI, with no automatic listening on restart.

Tests: actual local H264 and hardware-encoded AV1 WHIP ingestion, Opus stereo, native OMT
video/audio reception, RTCP clocks, bad key rejection, duplicate publisher rejection,
DELETE/reconnect. Remote-PC reception and subjective A/V/latency remain to be tested.

## 0.0.2.3: fixed-port direct WHIP reception

Receiver controls now select a receive port (default 56208) and optional public IPv4.
The receiver binds a separate HTTP endpoint and one BUNDLE UDP socket on that port.
The advertised public ICE candidate uses the same port, assuming a static 1:1
TCP and UDP port mapping to this PC. No STUN/TURN is needed for that mapping.
Local sender control stays on port 18879 and is not served on the public receive port.
The receiving endpoint is /api/whip-input and requires the configured Bearer key.
Start receiver explicitly; the receive key and settings persist but do not auto-start.
A public HTTP endpoint sends the Bearer key without HTTPS protection; the WebRTC
media remains DTLS/SRTP encrypted. Use an HTTPS reverse proxy for long-term public use.
Router and Windows firewall must allow the selected port for both protocols.

The fixed port adapter targets the pinned aioice / aiortc versions, rejects
unbundled offers, and preserves the same UDP port across sender reconnects.
Tests verify all advertised candidates use the selected port, the public srflx
candidate, H264/AV1 + Opus to native OMT, authentication and DELETE/PATCH/reconnect.
Real remote-network connectivity is still awaiting the user's test.

Installer root: fastvideo.settings.json, Start-Bridge.bat and Stop-Bridge.bat. Program folders
are hidden in Explorer by default. Helper scripts and uninstall files
are in scripts; documentation is in documents. Old flat helpers are removed during upgrade.

## Firewall help

Main contains input and outputs. Settings contains the receiver's TCP/UDP port and public IPv4. Help explains sender/receiver firewall rules and router forwarding. Configure Windows Firewall starts the local helper with a Windows administrator prompt and an explicit confirmation of program, role, ports, profiles and remote scope. It does not disable the firewall or configure the router.

Choose Sender, Receiver or Both on each computer. LAN rules apply to Private/Domain profiles and LocalSubnet; Internet rules include Public and remote Any. The helper targets the actual Bridge Python executable and updates its own stable rules on repeated runs. It does not remove existing block rules or configure a separate SRT listener or NDI/OMT applications. Download script for another PC generates a standalone helper; run it there and select that computer's Bridge Python executable if needed. Older receiver mode uses the control TCP port plus dynamic UDP.

Windows Firewall stays enabled if it was enabled. If it was disabled for a test, re-enable it manually and verify media after creating the rules.

## File update without reinstalling

Copy scripts/Update-Bridge.ps1 to the other computer desktop and run it with PowerShell. It defaults to source Z:\FastvideoBridge and installed destination C:\FastVideoBridge, independently of the script location. Confirm the source and destination dialog. It stages and verifies code, assets, helpers and gateway, backs up overwritten files under work/update-backups, stops the installed service and restores active inputs and outputs. Settings, users, keys, drivers, vendor DLLs and Python runtime are preserved. A runtime/dependency change still needs the installer. Update-Bridge.vbs runs the neighboring PS1 without a command window; copy both files together if using that launcher.

## Desktop launchers

Only Start-Bridge.bat and Stop-Bridge.bat remain as root launch commands. All helpers are in scripts. The BAT files can be copied to a desktop: they first look next to themselves, then C:\FastVideoBridge, then Z:\Fastvideo_whip for this development computer only when no local installation exists. For custom installation locations use the installer-created shortcuts. The installer offers both Start and Stop desktop shortcuts. The file updater retires old root launchers into its backup folder.

Start/Stop BAT launchers call scripts/Invoke-Bridge.ps1 directly; there is no VBS dependency. The updater refreshes the Bridge desktop BAT launchers. An existing C:\FastVideoBridge installation with missing helpers must be updated; the launcher will not silently start the NAS development copy instead.

## Project directory layout

The root contains fastvideo.settings.json, Start-Bridge.bat and Stop-Bridge.bat. Documentation and dependency lists are in documents. The HTTP server entry is scripts/app.py; its project root remains the parent directory. Application modules, assets, runtime, work and user data keep their own directories. Keep SDK licenses in the bundled vendor/runtime directories.

## Production presets

Settings > Production presets saves named input source and output forms, including destinations, keys, bitrates, audio and quality settings. Preset payloads are DPAPI encrypted under user/productions. They remain local to the Windows account and machine. Load stops input and all outputs before applying destinations; no stream is started by loading. The active production name is persistent and displayed below the central title on Main. Hardware selection and internal server ports are not changed by a production preset.

## Blackmagic SDI input and output

Install current Blackmagic Desktop Video on each computer with a DeckLink card.
Bridge calls the installed DeckLink COM API directly. No SDI2OMT process or
additional video encoder is used for SDI output. Card profiles and routing are
left as configured in Desktop Video; busy connectors are not taken over.

Input: Settings > Find sources > SDI / DeckLink connector > Set source.
Progressive input format and frame rate are detected automatically. Embedded
audio channels 1 and 2 are used as stereo. Interlaced input is not supported in
this first implementation; change the source to progressive 720p or 1080p.

Output: receive card > Blackmagic SDI > choose a free output connector.
Both WHIP input and FastVideo-link/WHEP reception can output to SDI. The received
progressive format must be supported by the selected connector. If it is not,
choose the manual 720p output format or change the source. SDI uses UYVY 8-bit
video and embedded 48-kHz / 32-bit stereo audio. The card paces both tracks on
one scheduled hardware timeline, starting with two queued pictures. RTCP source
clock anchoring is kept stable during each connection to avoid moving scheduled
pictures when sender-report arrival timing varies. Reconnection resets and
prerolls a fresh hardware timeline. The hardware queue is bounded to six frames;
this is a burst limit, not an additional fixed six-frame startup delay.

Connector numbers refer to the driver's device list. Check the chosen connector
again when loading a production on another computer or after changing a card's
profile. A Quad 2 / Duo 2 must have the desired input/output routing configured
in Desktop Video. No network or router port is needed for the SDI cable itself.
Late/dropped SDI pictures and audio output drops appear in receiver diagnostics
and trigger a temporary orange warning when the counters increase.


## Status sampling (0.0.2.42)

The control page reads one shared dashboard sample per second. Status and warning calculations use a dedicated background worker; warning files are written there as well. Network addresses refresh every 30 seconds in that worker. Reloading the page reuses discovered sources and SDI connectors; Find sources and Refresh SDI still explicitly rescan hardware. Missing status updates are reported after four seconds rather than showing indefinitely stale values. Media clocks, audio grace, SDI preroll and warning thresholds are unchanged.

A local 720p50 SDI -> WHIP H264 -> SDI test with browser reloads observed 79 seconds of connected operation: 50 fps, no additional missing audio samples, queue losses or late/dropped SDI frames. Decoder p95 2.47-2.81 ms; output p95 1.49-1.72 ms. This is a short regression check, not a new one-hour reliability result. 25 backend tests passed.

## Version 0.0.2.42: native SDI input preparation

SDI capture retains an owned UYVY frame. The encoder preparation worker converts
it directly to NV12, avoiding the previous UYVY -> BGR -> NV12 round trip.
Capture drains audio without waiting for video colour conversion. The preview
converts only its 240x135 thumbnail; timestamps or letterboxing are processed
on the video worker when requested. Each encoder keeps private output pixels.

The previous five-minute .102 measurement showed 293,760 missing audio sampleframes
and 221 skipped VideoTrack frames. A repeat with the same SDI source and encoder
settings is required before claiming these field errors are resolved.

## Version 0.0.2.42: sender encoder pipeline

The shared publisher prepares at most one next picture while the hardware
encoder handles the current picture. No growing frame FIFO is introduced;
source timestamps and each encoder's serial order are retained. Stop waits
for active encoding and cancels the pending source read.
H264 NVENC now accepts the existing NV12 image directly, eliminating the
implicit NV12 -> planar YUV420 conversion inside the encoder process.
Quality, bitrate, lookahead and frame cadence settings are unchanged.
First verify 720p50 on .102, then test physical 1080p50 input separately.

## Version 0.0.2.43: SDI playback startup

SDI scheduled playback waits for three video frames and nonempty stereo audio before ending audio preroll. Either media worker can complete startup, so video arriving first cannot start an empty audio queue. Hardware output losses remain visible in the receiver and sender warning indicators.

## Version 0.0.2.44: independent SDI colour preparation

The dedicated video worker converts decoded video to UYVY before entering the shared DeckLink scheduling lock. Audio can schedule during this CPU conversion. Card access, format changes and the audio/video hardware timeline remain serialized. Stage timings distinguish lock wait, colour conversion, allocation, copy and driver scheduling; their rolling samples remain bounded in memory. No additional media queue or playout delay is added.

## Version 0.0.3.0: separate processes, hardware paths, measured below 10 ms

Measured at 1080p50, 4 Mbit/s, sender and receiver running at the same time (p95):
laptop (i9-14900HX, RTX 4080 Laptop) sender 3.8 ms, receiver decode 2.7 ms, output 2.0 ms;
Intel NUC (i7-8705G) sender 5.5 ms, receiver decode 2.3 ms, output 4.0 ms; SDI output without drops.

- Receiver runs in its own processes: network (WebRTC) and media (decoder, audio, SDI/OMT/NDI output).
  Only compressed pictures and decoded audio cross between them (fastvideo/receiver_process.py, receive_media.py).
- NVENC and Quick Sync encode in the main process; the encoder subprocess round trip cost several ms per frame.
- New WHIP encoder quality "veryfast" (Quick Sync) for the lowest delay.
- Receive decoding prefers the native Quick Sync decoder with gpu_copy; D3D11VA held the GIL during copy-back.
- Every Bridge process opts out of Windows power throttling (fastvideo/process_priority.py); started by
  double-click, Windows 11 had moved the windowless processes to efficiency cores.
- Capture loop sleeps until the next picture deadline instead of waking every millisecond; large frame copies use NumPy (GIL released).
- SDI output: sequential slot scheduling, playback start two frames behind the newest picture, lead trimming
  (one picture plus 20 ms audio) and re-anchoring after source restarts.
- Bitrate chosen in the WHIP dialog survives a source restart.
- Saved stream keys that cannot be decrypted on another PC load as empty instead of failing with HTTP 500.
- Help: "Optimise graphics card for Bridge" sets the NVIDIA profile "Prefer maximum performance" (scripts/Configure-GpuPerformance.ps1).
- Copy buttons for the WHIP URL / viewer link, the OMT/NDI source name and the example address in Help.

## Version 0.0.3.1: large pictures, decoder self-healing

Measured on an i9-10900X with Quadro RTX 4000, loop on the same PC, H.265 at 12 Mbit/s, 1080p50, 3 minutes:
before 5 output stalls of up to 20 s; after 0 stalls, 0 drops (8299 pictures sent, 8295 output).

- Receive buffer holds up to 2048 packets per video picture (about 2.4 MB) instead of aiortc's 128 (about 150 kB).
  Larger keyframes (H.265 at 12 Mbit/s up to 226 kB measured, H.264 from about 6 Mbit/s) were dropped without
  counting as packet loss and re-requested in a loop. Status shows `video_buffer` (overflows, largest picture).
- Receive decoder order: Quick Sync, then CUDA (NVIDIA, GIL held under 3 ms), then D3D11VA, then CPU.
  The native NVIDIA cuvid decoder is not used: it returns every picture one frame late (+20 ms).
  `FASTVIDEO_DECODERS=cuda,d3d11va` forces an order for tests.
- Joining a running stream waits for the next keyframe instead of falling through to the CPU decoder.
- Self-healing: a decoder that returns no picture for about one second is rebuilt (a second stall within 10 s
  moves to the next decoder); a decoder that never returns a picture after three keyframes is replaced;
  timestamps that jump backwards are followed instead of discarding every picture.
- Receiver warns "Video arrives but no pictures are output" instead of staying green; the media status thread
  can no longer stop on a division by zero; repeated decoder errors are logged once per 10 s.
- The receiver shows the incoming codec (for example "1920×1080p50 · H.265").

## Version 0.0.3.2: bitrate limit 10 Mbit/s

- WHIP and SRT offer 1–10 Mbit/s (was 12). NVENC stops at 10 Mbit/s anyway; 10 Mbit/s was measured without
  loss on all test machines. Settings saved earlier with up to 12 Mbit/s still start and run at 10.

## Version 0.0.4.0: hardening and clean-up

- `/api/status` without credentials only returns what viewers and a pulling WHEP Bridge need (title, logo flag,
  codecs, timestamp flag, source kind, application and version). Full diagnostics: on this PC, or from another PC
  with `Authorization: Bearer <WHIP receiver key>` of the running receiver. Requests through a tunnel get the
  public fields only.
- The Bridge identifies itself as "FastVideo Bridge" (was "FastVideo LAN 0.1", the first project name) and reports
  its version in `/api/status`. Start-Bridge.ps1 accepts both names during an update.
- Viewer diagnostics are accepted only from sessions the gateway knows.
- The SRT output logs why it stopped.
- The viewer shows "Source: SDI" for SDI inputs.
- New application icon `images/fastvideobridge.ico` (16–256 px) for setup, shortcuts, the uninstall entry and the
  browser tab (`/favicon.ico`); the installer no longer builds a single-size icon from the logo. The updater
  also copies `images`. The installation notes show the current version (they still said 0.0.2.44).
- Removed: the load test page (served without authentication), the `--test-delay-ms` option, archived development
  launchers (scripts/dev-legacy) and unused imports.
- Tests: stale NVENC, SDI output, preview and SRT tests updated; AV1 tests are skipped on PCs whose Intel graphics
  cannot encode AV1. Source history is kept in git (Z:\FastvideoBridge).

## Version 0.0.5.0: connection profiles, help window

- Connection profiles: the disk button in the WHIP, SRT and receive cards opens one dialog for that connection
  type. Save the current form as a named profile, load a profile into the form, overwrite, rename or delete.
  Profiles are stored per type in `user/profiles/<type>/`, encrypted with Windows DPAPI like production presets
  (readable only by the same Windows account on the same PC).
- Loading a profile only fills the form. A running output keeps its current settings; the card shows an orange
  note "loaded – not active yet" until the output is stopped and started again. Production presets (Settings)
  are unchanged and still load a whole production.
- Help opens in its own window, so closing the help with X no longer closes the Bridge controls.
- The receiver endpoint-name check works again in current browsers (its pattern was rejected as invalid).

## Version 0.0.5.1: loss counters start at 0 once synchronised

- After a receive connection starts, the output settles first (SDI lead trimming drops a few pictures on purpose,
  the first pictures can overflow a queue). The receiver shows "Synchronising output…" during that phase and no
  orange warning. Settled = no loss counter, trim or re-anchor for 3 s (at least 2 s after the start); after
  20 s the counters start anyway, so a lasting problem is never hidden.
- From then on all loss counters (receive queue video/audio, SDI video/audio drops, late and dropped frames)
  count from 0 - in the page, the status API and the remote status. Any number above 0 is a real loss.

## Version 0.0.5.2: findings from FastVideo

- SDI source discovery no longer fails sporadically with "thread mode cannot be changed": a worker thread that
  another library had already initialised for COM is accepted (the check was there but never reached).
- SDI input accepts interlaced and segmented signals: 1080i50 is deinterlaced field by field ("bob") to 50
  full pictures per second (about 20 ms more delay, about 0.4 CPU cores more); 1080PsF and 25p arrive as whole
  pictures. Before, the input stopped with "SDI input currently requires progressive video".
- Formats in broadcast notation: 1920x1080-50p, 1920x1080-50i · deinterlaced, 1920x1080-50psf.
- Timestamp marker written straight into the YUV picture instead of a full BGR copy (less CPU when the
  latency stripe is on); preview thumbnails reuse one scaler instead of building one per picture.
- Stop no longer aborts on a stale pid file that now belongs to another program (after a reboot).
- Settings files saved with a BOM (e.g. by PowerShell or some editors) are read correctly.

## Version 0.0.5.3: interlaced in and out

- NDI and OMT sources in 1080i (e.g. vMix 1080i50) are deinterlaced field by field like SDI: 50 full pictures
  per second instead of 25 combed ones. Before, 1080i over NDI/OMT left the Bridge as 25p.
- Receiver, Blackmagic SDI output: new option **SDI 1080 interlaced · 1080-50p → 1080-50i**. Two received
  pictures are woven into one interlaced frame. A source that was 1080i at the sender comes out with its
  original fields again. The field order is found from the pictures themselves, so a pairing that starts on
  the wrong field corrects itself once after about a second. 25p leaves as 1080i50 (both fields from one
  picture), 720p stays progressive. The option applies when the receiver starts.
- Field pacing uses the high-resolution clock (Windows' normal clock ticks only every 15.6 ms).

## Version 0.0.5.4: interlaced transport that holds on slower PCs

- Settings → Output format → **Interlaced sources**: *deinterlace to 50p* (as before, for browsers, vMix
  and progressive outputs) or *keep interlaced (25 frames)*: the 1080i picture is sent unchanged with
  both fields. A receiver with "SDI 1080 interlaced" outputs the original 1080i50. No field splitting
  or re-pairing, about half the work. Use it for SDI 1080i → SDI 1080i.
- NDI/OMT deinterlacing: the second field was sometimes replaced by the next frame before it was
  released (on .102, where splitting takes ~16 ms, only 30 of 50 pictures came out and the woven SDI
  output jumped). The next frame is now read only after the second field has gone out.
- Receiver: the picture rate is taken from the picture timestamps when the sender declares a higher
  rate than it sends (Bridge encoders declare the start rate, e.g. 50, also for 25 frames). Before,
  25 woven frames were paired as if they were 50p fields (12.5 frames per second on the SDI card).
- SDI output audio buffer follows the frame length (5 pictures; 200 ms at 25 frames). The fixed
  100 ms dropped about 13 audio packets per second at 25 frames.
- Receiver option renamed **Interlaced output · 1080i** and placed under the output selection; it now
  applies to SDI, NDI and OMT. 25 woven frames leave NDI/OMT marked as interlaced (vMix sees 1080i50);
  50p stays progressive there. SDI behaves as before.
- Sender output format shows 1920x1080-50i · fields kept (not 25p) when interlaced sources are kept.
- NDI input asks the SDK for fields: 1080i over NDI is now recognised as interlaced (before it was
  always reported progressive and passed as woven 25p). Single-field NDI frames become full pictures.
- SDI capture worker follows the Interlaced sources setting (it ran in its own process and kept splitting).

## Version 0.0.5.5: hardening, stream keys, no bundled NDI Tools

- WHIP receiver slows down key guessing: after 10 wrong stream keys from one address within a minute,
  that address gets "429 Too many requests" for a minute (direct WHIP port and the main port alike).
- Receiver: **Generate key** creates a random 43-character key (256 bit); keys under 16 characters
  show a "weak key" hint. **Copy for sender** copies address (public IP, port, endpoint) and key;
  pasted into the WHIP address field of the sending Bridge it fills both fields.
- Error handling: background clean-up tasks are kept until done and their failures logged; a failed
  WHIP offer is logged with its reason (cancellation is no longer turned into "400"); hardware encoder
  worker, H.265 WHIP and WHEP reception log the reason of a failure; viewer WHEP errors no longer send
  internal exception text to the browser.
- NDI Tools are no longer bundled (the NDI terms do not allow redistributing them). Install NDI Tools
  from https://ndi.video/tools/ for NDI input or output; the installer offers to open that page.
  The page shows the NDI® trademark notice. OMT and SDI work without NDI.

## Version 0.0.5.6: public address for senders

- Receiver network settings: **Detect** asks the Internet (STUN) for this location's public IPv4; Save
  stores it, so the receiver also announces it to Internet senders.
- **Copy for sender** now copies the Internet address (public IP) and, below it, the address in this
  network, then the key. Pasting it into a sender uses the Internet address. Without a saved public IP
  it copies the network address with a hint to Detect and Save.

## Version 0.0.5.7

- Receiver network settings: the public IPv4 can be saved while the receiver runs (used by the next
  sender connection); port and endpoint name still need a stop. Detect sits next to the IP field; the
  button is called **Save network settings** (it stores all three fields) and shows its result or
  error right below it.
- Copy for sender reads the current receiver state at the click, so a just saved public IP is included.

## Version 0.0.5.8

- H.264 (NVENC and Quick Sync) follows the picture rate of the source. The encoder assumed 50
  pictures per second for every source: with 25p, or 1080i kept as 25 interlaced frames, it sent
  about half the bitrate that was set (measured 2.3 instead of 4 Mbit/s) and a keyframe only every
  4 s; with 60p about 20 % too much. H.265 and SRT already followed the source.
- A source fault or a source change no longer disconnects the far end. The gateway keeps the WHIP
  output and the viewers for up to 10 s without media and continues the media time when the source
  pipeline restarts; the receiver freezes the last picture instead of reconnecting and synchronising
  its output again. A pipeline fault is noticed within 1 s (before: up to 10 s). A source that stays
  away longer than 10 s is handled as before.
- Receiver: a sender that reconnects after a network break replaces its old session once that
  session has received nothing for 3 s. Before, the receiver answered "A sender is already
  connected" for about 30 s. A session that still receives media is never replaced.
- Two receiver error messages showed garbled characters ("1-512 characters", "1-200 bytes").

## Version 0.0.5.9

- SDI input and SDI output can share one full-duplex Blackmagic device (e.g. UltraStudio HD Mini).
  The Bridge refused a device as soon as anything used it: with the output playing (from the
  receiver or from vMix) the input reported "connector is already in use". Now only the same
  direction blocks; devices that are not full duplex behave as before. The output list marks a
  connector "in use" only when its output is taken.
- Stopping the input shows **Source stopped** at the WHIP output instead of receiver and transport
  warnings, and the far end is released at once (it returns to "waiting") instead of warning about
  missing picture and sound. H.265 no longer logs an encoder error when the input is stopped.

## Version 0.0.5.10

- The input badge under the preview shows the format that was read, e.g. **Input 1080p50** or
  **Input 1080i50**, instead of "Signal present". Without a picture it still says "No signal".

## Version 0.0.5.11

- SDI output follows a card clock that runs faster than the source. Source and card run on
  separate clocks; a faster card used up the lead, and from then on every picture arrived too
  late until the output resynchronised at -250 ms. Now the schedule moves one picture later as
  soon as the lead falls below one picture (the card repeats one picture, the sound pauses for the
  same time, in sync). The other direction (lead above three pictures) was already handled.
  Status: `lead_pads` next to `lead_trims`.
- Receiver: when the process for decoder and output ends by itself, a new one is started and the
  sender reconnects by itself. Before, only a warning appeared until the receiver was restarted.
- Receiver: at most 100 compressed pictures wait for the decoder. A decoder that stays behind no
  longer builds up delay without limit; the waiting pictures are discarded and decoding continues
  at the next keyframe ("Decoder cannot keep up · pictures skipped").
- Update-Bridge.ps1 asks the Bridge and its process whether it has stopped. The former port check
  finds no listening port on some Windows builds and let a running Bridge pass.
- A source that stays away writes its error to the log once and then one line every five minutes,
  not a full error block every 10 s. At start, OMT logs older than 7 days are removed and
  append-only logs above 5 MB start a new file.

## Version 0.0.5.12

- Settings, **Input audio**: choose which channels of the source are sent, as in FastVideo: a stereo
  pair (1 / 2, 3 / 4, 5 / 6, 7 / 8) or one channel as mono on both sides. SDI now reads the eight
  embedded channels of groups 1 and 2 (before: only 1 / 2); NDI and OMT offer the channels the
  source delivers. The choice is applied at once, without a restart, and is saved. If the source
  does not have the selected channels, 1 / 2 is sent and the page says so.

## Version 0.0.5.13

- Settings: **Input audio** sits in the Input source card, to the right of the source choice (below
  it on narrow windows), instead of in a card of its own.

## Version 0.0.5.14

- Settings: **Connection log** with the last 100 connections, newest first: start, duration, type
  (WHIP send, SRT send, WHIP / WHEP receive), result, amount and the other side. Sending shows the
  destination address, receiving the address the sender connected from. A series of failed attempts
  to one address is one line. **Text log** opens the same list as plain text
  (`/api/connection-log?format=text`); it is stored in `work\connections.json`.
- Settings, WHIP receiver network: the addresses senders use (URL with the public IPv4, LAN) are
  shown beside the fields, with **Copy for sender**; the router rule is shown as in FastVideo
  (TCP + UDP, external port, this PC, internal port).
- Main: the cards are named **WHIP sender** and **SRT sender**. The input badge shows the audio
  choice behind the picture format (Input 1080p50 · Audio 1+2); the Audio line in Diagnostics is gone.
- Settings, Input audio: the channel text stands beside the title (it was cut off below the list).
- Diagnostics, CPU / GPU: the graphics card the encoder runs on is named first. Laptops that show
  their picture through the processor graphics listed only that one, not the NVIDIA card.
