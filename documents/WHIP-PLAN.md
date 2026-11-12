# FastVideo WHIP – isolated development copy

Location: Z:\FastvideoBridge (was Z:\Fastvideo_whip). Z: is the NAS Cloud share.
Original project: C:\Fastvideo. The original service has not been stopped or modified for this copy.

## Current state

This is a verified copy of the current working FastVideo code. The WHIP output is not implemented yet. Source-file SHA256 hashes are recorded in work/copy-baseline.json. Production user access links, runtime process IDs, old logs, saved source selection and the Python virtual environment were not copied. The event-specific usermail.txt is present.

## LAN test configuration

- HTTP / sender UI: 18879
- WebRTC gateway: 18880
- Local encoded-media bridge: 19999
- Public address, Cloudflare URL and STUN list cleared for the local experiment.
- Router forwarding is unnecessary for this LAN test. Windows Firewall access may be needed when another LAN computer connects.
- Separate ports avoid conflicts with the running FastVideo on the same PC; they are not router settings.
- Do not run C:\Fastvideo launchers to start this project. Launchers in this copy derive their root from their own location.
- Before running the copy, use its Setup-FastVideo.cmd to create its own environment. For sustained streaming, a separate local working copy is preferable to execution over the NAS.
- Starting a second encoder shares the GPU and CPU with production. No second pipeline was started merely to verify this copy.

## Code review and next implementation

The current path is Engine -> Publisher/Quick Sync -> GatewayBridge (FV01 encoded frames) -> Go/Pion RTP tracks. The gateway already has bounded media queues, keyframe gating, RTCP feedback and shared audio/video clock mapping. Reuse these for an outbound WHIP peer; no additional decode or encode is needed.

The existing WHEP handler receives an offer and creates an answer. The WHIP sender must instead create an offer, gather ICE candidates, POST application/sdp to the receiver and apply its answer. Add optional Bearer authentication, Location resolution, DELETE session cleanup, timeouts, RTCP/keyframe feedback and reconnect. Preserve the shared clock and do not log access keys.

Start with one selected video codec plus stereo Opus. Do not assume that one WHIP ingest automatically supplies both AV1 and H264 to downstream viewers; the receiver must explicitly support that workflow.

Use a separate Broadcast Box as the local WHIP receiver / WHEP distributor. Check and pin its source revision before building; select free test ports. First H264 4 Mbit/s, then AV1 2 Mbit/s if supported. Compare source and received browser video using a simultaneous phone photo. Measure frame rate, packet loss, buffering, encode time and AV sync. Test receiver restart and network interruption.

References:
- https://www.rfc-editor.org/rfc/rfc9725.html
- https://github.com/Glimesh/broadcast-box
- https://github.com/kevmo314/whep-to-whip

whep-to-whip is a signaling-only demonstration. It expects the WHEP source to generate an offer after an empty POST, unlike this project's WHEP handler. Use the direct source-to-sink idea rather than running this demonstration unchanged.

Implementation update: The WHIP output is now implemented. See WHIP-README.md for launch instructions, current validation and remaining limitations. The earlier baseline description above records the state at initial copy time.
