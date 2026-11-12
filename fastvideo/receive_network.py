"""Pinned-aiortc RTP counters: distinguish media SSRCs from RTX repair SSRCs.

aiortc 1.x getStats uses one report ID for all streams of a receiver, so the
last RTX stream overwrites the primary video report. Read its existing counters
without altering reception, RTCP feedback or sequence numbers.
"""
async def receive_network(pc):
    rows=[]
    for receiver in pc.getReceivers():
        streams=getattr(receiver,'_RTCRtpReceiver__remote_streams',None)
        if streams is None:
            stats=await receiver.getStats()
            rows.extend(dict(kind=r.kind,ssrc=r.ssrc,packets_received=r.packetsReceived,
                             packets_lost=r.packetsLost,jitter=r.jitter)
                        for r in stats.values() if r.type=='inbound-rtp')
            continue
        repairs=getattr(receiver,'_RTCRtpReceiver__rtx_ssrc',{})
        kind=receiver.track.kind if receiver.track else 'unknown'
        for ssrc,stream in streams.items():
            if ssrc in repairs:continue
            rows.append(dict(kind=kind,ssrc=ssrc,packets_received=stream.packets_received,
                             packets_lost=stream.packets_lost,jitter=stream.jitter,
                             jitter_ms=round(stream.jitter*1000/stream._clockrate,3),
                             retransmission_packets_received=sum(s.packets_received for repair,s in streams.items() if repairs.get(repair)==ssrc)))
    return rows
