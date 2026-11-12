# GoSRT compatibility patch

Pinned dependency: github.com/datarhei/gosrt v0.12.0 (MIT), vendored by Go.

The local libsrt reference receiver rejected payload-free ACKACK control packets
with `incoming UMSG: 6 INVALID SIZE: 0`. Media still arrived, but its acknowledgement
of acknowledgement was ignored. FastVideo Bridge adds one unused zero uint32 to
ACKACK, keeping the acknowledgement number in TypeSpecific. This matches libsrt's
control-packet validation and prevents the errors. No encryption or media payload
is changed. Reapply and retest this patch after regenerating vendor.

Validation: caller transfers against GoSRT and a libsrt reference receiver, without
encryption and with AES 128/192/256, plus decoded H264/AAC media checks.
