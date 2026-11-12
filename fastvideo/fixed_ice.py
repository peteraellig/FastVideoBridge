"""Pinned aioice adapter: one BUNDLE UDP socket and explicit static NAT mapping."""
import asyncio
import socket
from aioice.ice import StunProtocol
from aioice.candidate import Candidate,candidate_foundation,candidate_priority

def reserve_udp(port):
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024*1024)
        sock.bind(('0.0.0.0',port));sock.setblocking(False)
        return sock
    except BaseException:sock.close();raise

def attach_fixed_ice(pc,owner):
    connections=set()
    for transceiver in pc.getTransceivers():
        connection=transceiver.receiver.transport.transport.iceGatherer._connection
        if connection in connections:continue
        connections.add(connection)
        async def gather(component,addresses,timeout=5,connection=connection):
            if component!=1:raise RuntimeError('BUNDLE and RTCP mux are required')
            sock=owner.udp_socket or reserve_udp(owner.listen_port);owner.udp_socket=None
            transport,protocol=await asyncio.get_running_loop().create_datagram_endpoint(lambda:StunProtocol(connection),sock=sock)
            hosts=[a for a in addresses if ':' not in a] or ['127.0.0.1']
            candidates=[Candidate(foundation=candidate_foundation('host','udp',ip),component=1,transport='udp',priority=candidate_priority(1,'host'),host=ip,port=owner.listen_port,type='host') for ip in hosts]
            protocol.local_candidate=candidates[0];connection._protocols.append(protocol)
            if owner.public_ip:
                candidates.append(Candidate(foundation=candidate_foundation('srflx','udp',owner.public_ip),component=1,transport='udp',priority=candidate_priority(1,'srflx'),host=owner.public_ip,port=owner.listen_port,type='srflx',related_address=hosts[0],related_port=owner.listen_port))
            return candidates
        connection.get_component_candidates=gather
