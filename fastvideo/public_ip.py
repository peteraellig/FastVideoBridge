"""Public IPv4 of this site for the receiver's WHIP address (Detect button). Same as FastVideo."""
import socket


def detect_public_ipv4(server=('stun.cloudflare.com', 3478), timeout=3.0):
    """Public IPv4 of this site via one STUN binding request (RFC 5389, XOR-MAPPED-ADDRESS)."""
    import os
    import struct
    transaction = os.urandom(12)
    request = struct.pack('!HHI', 0x0001, 0, 0x2112A442) + transaction
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.settimeout(timeout)
        for _ in range(2):
            probe.sendto(request, server)
            try:
                answer, _ = probe.recvfrom(2048)
            except socket.timeout:
                continue
            if len(answer) < 20 or answer[8:20] != transaction:
                continue
            position, end = 20, 20 + struct.unpack('!H', answer[2:4])[0]
            while position + 4 <= min(end, len(answer)):
                kind, length = struct.unpack('!HH', answer[position:position + 4])
                value = answer[position + 4:position + 4 + length]
                if kind in (0x0020, 0x0001) and len(value) >= 8 and value[1] == 1:
                    raw = struct.unpack('!I', value[4:8])[0]
                    if kind == 0x0020:
                        raw ^= 0x2112A442
                    return socket.inet_ntoa(struct.pack('!I', raw))
                position += 4 + length + (-length % 4)
    raise OSError('No answer from the STUN server (outgoing UDP blocked?)')
