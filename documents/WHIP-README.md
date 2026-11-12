# FastVideo WHIP Sender – LAN test

This is a separate development copy. Original FastVideo source files and its running service are unchanged.

## Open and use

Run **Start-Whip.cmd**, then open http://127.0.0.1:18879/sender#whip.

1. In Setup, select an OMT/NDI source and click Set source. The current test source is K12700 (vMix - Output 1).
2. In WHIP output, enter the receiver endpoint and stream key. Start WHIP also starts the confirmed input if it is stopped.
3. Select H264 for the first test. This uses the existing 4 Mbit/s QSV fallback plus shared stereo Opus. AV1 uses the main 2 Mbit/s QSV stream.
4. Click Start WHIP. Stop WHIP terminates the outbound session and cancels retries; the input preview/encoder stays available until Stop streaming in Dashboard.
5. Open the receiver's viewer page to watch the result.

Current Broadcast Box target:

- WHIP endpoint: http://192.168.1.102:8050/api/whip
- Stream key: use the supplied token in Stream key / bearer token; it is deliberately not stored in this document.
- Viewer: open Broadcast Box at http://192.168.1.102:8050 and use the matching stream key.

The endpoint was not reachable during preparation. The sender can keep retrying and should connect when the receiver is running and reachable.

## Runtime isolation

HTTP/control 18879; gateway control/media 18880; loopback encoded-media bridge 19999. No router port forwarding or Cloudflare is required for this LAN test. Receiver HTTP and WebRTC UDP must be reachable through its host firewall; Docker may also require UDP mapping and LAN candidate-address configuration.

Start-Whip.ps1 uses this copy's virtual environment when present. On this PC it can otherwise reuse C:\Fastvideo\.venv\Scripts\python.exe solely as the interpreter/library runtime, while running this copy's code, DLLs, settings and process supervisor. It does not modify the original environment or files. For another machine, run Setup-FastVideo.cmd in this project first.

Use Stop-Whip.cmd to stop only this experiment. Token is retained in gateway memory while enabled, is not saved in settings, and is cleared on Stop. After restart, enter the token again. The UI's password field contains it until the page closes or the operator clears it. Use HTTPS for a remote receiver.

## Implementation and validation

- Outbound send-only Pion peer, standard WHIP application/sdp POST/201 answer and Location session cleanup with DELETE.
- Full candidate gathering before POST; no trickle ICE/PATCH or TURN-server discovery in this initial LAN version.
- Existing encoded-media bridge, bounded queues, PLI/keyframe recovery, retransmission interceptors, shared RTP/NTP audio/video clock.
- Reconnect after transport/session failure; wait for fresh source frames before connecting.
- Cross-origin HTTP redirects rejected to avoid forwarding credentials. Use the receiver's final endpoint URL.
- Token absent from status and logs; management API restricted to localhost like the original sender.
- Go integration test exchanges actual H264 and Opus RTP with a local Pion WHIP receiver, checks retry after HTTP 503, reconnect after media-peer loss, authenticated DELETE cleanup and credential redirect rejection.
- Python users/capacity/source checks pass. Actual OMT input and both QSV streams reach the isolated gateway at about 50 fps.

Actual displayed video, sound, AV synchronization and latency through the second PC's Broadcast Box still require the live test. RTP packet tests alone do not establish decoded picture quality or latency.

No automatic bitrate adaptation, TURN provisioning or duplicate-encoder elimination is implemented yet. The copied pipeline currently prepares AV1 and H264 once each; selecting H264 for WHIP does not stop the AV1 encoder.

