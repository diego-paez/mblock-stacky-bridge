"""Makeblock CyberPi "F3" live-mode framing (USB serial, 115200 8N1).

Reimplemented from Makeblock's `makeblock` pip package
(protocols/PackData.py: HalocodePackData, protocols/Protocols.py: HalocodeProtocol).
The host sends MicroPython source; CyberPiOS executes it and, for
RUN_WITH_RESPONSE, replies with a Python-literal dict such as {"ret": 42}.

Frame:
  F3 | hchk | len_lo len_hi | type | mode | idx_lo idx_hi | data... | chk | F4
  hchk = (F3 + len_lo + len_hi) & 0xFF
  len  = len(data) + 4
  chk  = (type + mode + idx_lo + idx_hi + sum(data)) & 0xFF
  For TYPE_SCRIPT, data = script_len_lo script_len_hi utf8(script)

Kept dependency-free so it can be ported to C++ for the CoreS3.
"""

HEADER = 0xF3
FOOTER = 0xF4

TYPE_SCRIPT = 0x28
TYPE_SUBSCRIBE = 0x29
TYPE_ONLINE = 0x0D
TYPE_UPLOAD_MSG = 0x03  # "upload mode message": JSON {"message","value"} + NUL, no mode/idx

MODE_RUN_WITHOUT_RESPONSE = 0x00
MODE_RUN_WITH_RESPONSE = 0x01

# Taken verbatim from makeblock api_cyberpi_api.goto_online_mode / goto_offline_mode
GOTO_ONLINE = bytes([0xF3, 0xF6, 0x03, 0x00, 0x0D, 0x00, 0x01, 0x0E, 0xF4])
GOTO_OFFLINE = bytes([0xF3, 0xF6, 0x03, 0x00, 0x0D, 0x00, 0x00, 0x0E, 0xF4])


def encode_script(script: str, idx: int, with_response: bool = True) -> bytes:
    body = script.encode("utf-8")
    data = bytes([len(body) & 0xFF, (len(body) >> 8) & 0xFF]) + body
    mode = MODE_RUN_WITH_RESPONSE if with_response else MODE_RUN_WITHOUT_RESPONSE
    length = len(data) + 4
    lo, hi = length & 0xFF, (length >> 8) & 0xFF
    idx_lo, idx_hi = idx & 0xFF, (idx >> 8) & 0xFF
    chk = (TYPE_SCRIPT + mode + idx_lo + idx_hi + sum(data)) & 0xFF
    return bytes([HEADER, (HEADER + lo + hi) & 0xFF, lo, hi, TYPE_SCRIPT, mode, idx_lo, idx_hi]) + data + bytes([chk, FOOTER])


def _wrap(payload: bytes) -> bytes:
    lo, hi = len(payload) & 0xFF, (len(payload) >> 8) & 0xFF
    return bytes([HEADER, (HEADER + lo + hi) & 0xFF, lo, hi]) + payload + bytes([sum(payload) & 0xFF, FOOTER])


def encode_message(message: str, value="") -> bytes:
    """Upload-mode message, received on the CyberPi by
    @cyberpi.event.upload_broadcast(message) / cyberpi.upload_broadcast.get(message)
    (mBlock block: "when I receive upload mode message").
    Framing from d-trattner/pi-codey tools/trigger_codey.py (Codey Rocky, same OS family)."""
    import json
    body = json.dumps({"message": message, "value": str(value)}, separators=(",", ":"), ensure_ascii=False)
    return _wrap(bytes([TYPE_UPLOAD_MSG]) + body.encode("utf-8") + b"\x00")


class Frame:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.type = raw[4]
        if self.type == TYPE_UPLOAD_MSG:
            self.mode = self.idx = None
            self.text = bytes(raw[5:-2]).rstrip(b"\x00").decode("utf-8", "replace")
            return
        self.mode = raw[5]
        self.idx = raw[6] | (raw[7] << 8) if self.type == TYPE_SCRIPT else None
        # Matches makeblock: script/subscribe replies carry text after 3 bytes of the payload
        start = 7 if self.type == TYPE_SCRIPT else 5
        self.text = bytes(raw[start + 3:-2]).decode("utf-8", "replace")

    def __repr__(self):
        return f"Frame(type=0x{self.type:02x}, idx={self.idx}, text={self.text!r})"


class Decoder:
    """Feed bytes, get complete frames. Same resync rule as makeblock's parser."""

    def __init__(self):
        self._buf = bytearray()
        self._len = None

    def feed(self, data: bytes):
        frames = []
        for b in data:
            self._buf.append(b)
            buf = self._buf
            if len(buf) > 3 and buf[-4] == HEADER and ((buf[-1] + buf[-2] + buf[-4]) & 0xFF) == buf[-3]:
                self._buf = bytearray(buf[-4:])
                self._len = self._buf[2] | (self._buf[3] << 8)
            elif self._len is not None and len(self._buf) == self._len + 6:
                if self._buf[-1] == FOOTER:
                    frames.append(Frame(bytes(self._buf)))
                self._buf = bytearray()
                self._len = None
        return frames
