"""Pure Art-Net, sACN and DDP decoders and OSC encoding."""

import math
import struct

from .constants import DDP_CHANNELS
from .values import integer


def osc_string(value):
    if not isinstance(value, str) or '\0' in value:
        raise ValueError('OSC strings must be text without null characters.')
    data = value.encode('utf-8') + b'\0'
    return data + b'\0' * (-len(data) % 4)


def encode_osc(address, args):
    if not isinstance(address, str) or not address.startswith('/') or any(c.isspace() for c in address):
        raise ValueError('OSC address must start with / and contain no whitespace.')
    if not isinstance(args, list) or len(args) > 64:
        raise ValueError('Arguments must be an array with at most 64 entries.')
    tags, payload = ',', b''
    for arg in args:
        if not isinstance(arg, dict):
            raise ValueError('Each argument needs a type and value.')
        kind, value = arg.get('type'), arg.get('value')
        if kind == 'i':
            payload += struct.pack('>i', integer(value, -2147483648, 2147483647, 'OSC integer'))
        elif kind == 'f':
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 3.402823466e38:
                raise ValueError('OSC float must be a finite 32-bit number.')
            payload += struct.pack('>f', value)
        elif kind == 's':
            payload += osc_string(value)
        elif kind not in ('T', 'F'):
            raise ValueError('Argument type must be i, f, s, T, or F.')
        tags += kind
    packet = osc_string(address) + osc_string(tags) + payload
    if len(packet) > 8192:
        raise ValueError('OSC message is too large (maximum 8192 bytes).')
    return packet


def parse_artdmx(data):
    if len(data) < 18 or data[:8] != b'Art-Net\0' or data[8:10] != b'\x00\x50':
        return None
    if int.from_bytes(data[10:12], 'big') < 14 or data[15] & 0x80:
        return None
    length = int.from_bytes(data[16:18], 'big')
    if not 2 <= length <= 512 or length % 2 or len(data) < 18 + length:
        return None
    return int.from_bytes(data[14:16], 'little'), list(data[18:18 + length])


def parse_e131(data):
    """Accept unsynchronized E1.31 DMX data; reject preview and termination packets."""
    if len(data) < 126 or data[:16] != b'\x00\x10\x00\x00ASC-E1.17\x00\x00\x00':
        return None
    if data[18:22] != b'\x00\x00\x00\x04' or data[40:44] != b'\x00\x00\x00\x02':
        return None
    count = int.from_bytes(data[123:125], 'big')
    end = 125 + count
    if not 1 <= count <= 513 or len(data) < end:
        return None
    for offset in (16, 38, 115):
        field = int.from_bytes(data[offset:offset + 2], 'big')
        if field >> 12 != 7 or (field & 0xfff) != end - offset:
            return None
    if data[117:123] != b'\x02\xa1\x00\x00\x00\x01' or data[125] != 0:
        return None
    universe = int.from_bytes(data[113:115], 'big')
    if not 1 <= universe <= 63999 or data[108] > 200 or data[112] & 0xe0 or data[109:111] != b'\x00\x00':
        return None
    return universe, list(data[126:end])


def parse_ddp(data):
    """DDP v1 output ID 1/all, 8-bit data. A push commits the staged frame."""
    if len(data) < 10 or data[0] & 0xc0 != 0x40 or data[0] & 0x2e or data[1] & 0xf0:
        return None
    # Undefined or explicitly 8-bit RGB/HSL/RGBW/grayscale. No JSON/control/DMX-transit IDs.
    if data[2] not in (0, 0x0b, 0x13, 0x1b, 0x23) or data[3] not in (1, 255):
        return None
    header = 14 if data[0] & 0x10 else 10
    if len(data) < header:
        return None
    # A zero timecode means immediate. Scheduled timecode playback is not supported.
    if header == 14 and data[10:14] != b'\x00' * 4:
        return None
    offset, length = struct.unpack('>IH', data[4:10])
    if len(data) < header + length or offset + length > DDP_CHANNELS:
        return None
    return offset, list(data[header:header + length]), bool(data[0] & 1)

