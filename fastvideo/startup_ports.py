"""Choose an available pair of private TCP media ports before starting children."""
import socket

def available_pair(port, excluded=()):
    if port==19999 or port in excluded or port+1 in excluded or 20000 in (port,port+1):return False
    sockets=[]
    try:
        for number in (port,port+1):
            s=socket.socket();sockets.append(s)
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            s.bind(('127.0.0.1',number))
        return True
    except OSError:return False
    finally:
        for s in sockets:s.close()

def select_pair(preferred, excluded=()):
    candidates=[preferred]+list(range(20999,21999,2))
    for port in candidates:
        if type(port) is int and 1024<=port<=65534 and available_pair(port,excluded):return port
    raise RuntimeError('No available internal media port pair. Close conflicting applications.')
