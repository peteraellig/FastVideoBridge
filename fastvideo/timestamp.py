"""Frame-bound clock marker. 16-bit magic, 48-bit Unix ms, CRC16-CCITT.

The black/white marker is deliberately inside the encoded picture, so no
separate message can be mistaken for the timestamp of the displayed frame.
"""
import binascii
import numpy as np

MAGIC = b"\xfa\x57"
CELL = 16
WIDTH = 1280
HEIGHT = 16


def payload(timestamp_ms: int) -> bytes:
    body = MAGIC + timestamp_ms.to_bytes(6, "big")
    return body + binascii.crc_hqx(body, 0xFFFF).to_bytes(2, "big")


def stamp(image: np.ndarray, timestamp_ms: int) -> None:
    bits = np.unpackbits(np.frombuffer(payload(timestamp_ms), dtype=np.uint8))
    image[:HEIGHT, :WIDTH, :] = np.repeat(bits * 255, CELL)[None, :, None]


def stamp_yuv(frame, timestamp_ms: int) -> None:
    """Same marker written straight into a yuv420p/nv12 VideoFrame (limited range, like the
    BGR->YUV conversion of the BGR stamp). Only the top 16 rows are touched; the picture is
    never converted to BGR for it."""
    if frame.width < WIDTH or frame.height < HEIGHT:
        return
    bits = np.unpackbits(np.frombuffer(payload(timestamp_ms), dtype=np.uint8))
    luma = np.where(np.repeat(bits, CELL) == 1, 235, 16).astype(np.uint8)
    y = frame.planes[0]
    np.frombuffer(y, np.uint8).reshape(-1, y.line_size)[:HEIGHT, :WIDTH] = luma
    # Neutral chroma under the marker (4:2:0: 8 chroma rows cover 16 luma rows).
    chroma_width = WIDTH if frame.format.name == 'nv12' else WIDTH // 2
    for plane in frame.planes[1:]:
        np.frombuffer(plane, np.uint8).reshape(-1, plane.line_size)[:HEIGHT // 2, :chroma_width] = 128


def read_marker(image: np.ndarray) -> int | None:
    bits = []
    for i in range(80):
        v = float(image[6:10, i * CELL + 6:i * CELL + 10, :3].mean())
        if 65 <= v <= 190:
            return None
        bits.append(int(v > 127))
    packet = np.packbits(bits).tobytes()
    if packet[:2] != MAGIC:
        return None
    if binascii.crc_hqx(packet[:8], 0xFFFF) != int.from_bytes(packet[8:], "big"):
        return None
    return int.from_bytes(packet[2:8], "big")
