"""Bounded, inert meter-photo attachments. Image bytes never enter the journal."""

import base64
import binascii
import hashlib
import struct

from .account import AccountError

MAX_PHOTO_BYTES = 1024 * 1024
MAX_PHOTO_ENCODED = 4 * ((MAX_PHOTO_BYTES + 2) // 3)
MAX_DRAFT_PHOTO_BYTES = 16 * MAX_PHOTO_BYTES


class PhotoError(AccountError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _dimensions(raw, mime):
    """Inspect raster headers without decompressing untrusted image data."""
    if mime == "image/png":
        if (len(raw) < 45 or raw[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                or raw[-12:] != b"\x00\x00\x00\x00IEND\xaeB`\x82"):
            raise ValueError
        return struct.unpack(">II", raw[16:24])
    if mime != "image/jpeg" or raw[:2] != b"\xff\xd8" or raw[-2:] != b"\xff\xd9":
        raise ValueError
    offset = 2
    while offset < len(raw) - 2:
        if raw[offset] != 255:
            raise ValueError
        while offset < len(raw) and raw[offset] == 255:
            offset += 1
        marker = raw[offset]
        offset += 1
        if marker in (0xD9, 0xDA):
            break
        length = int.from_bytes(raw[offset:offset + 2], "big")
        if length < 2 or offset + length > len(raw):
            raise ValueError
        if marker in (0xC0, 0xC1, 0xC2):
            if length < 8:
                raise ValueError
            height, width = struct.unpack(">HH", raw[offset + 3:offset + 7])
            return width, height
        offset += length
    raise ValueError


def validate_photo(value):
    if not isinstance(value, dict) or set(value) != {"data", "mime_type"}:
        raise PhotoError("invalid_photo")
    data, mime = value["data"], value["mime_type"]
    if not isinstance(data, str) or not data or len(data) > MAX_PHOTO_ENCODED:
        raise PhotoError("photo_too_large" if isinstance(data, str) and data else "invalid_photo")
    try:
        raw = base64.b64decode(data, validate=True)
        if len(raw) > MAX_PHOTO_BYTES:
            raise PhotoError("photo_too_large")
        width, height = _dimensions(raw, mime)
        if not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > 20_000_000:
            raise ValueError
    except (ValueError, TypeError, IndexError, struct.error, binascii.Error):
        raise PhotoError("invalid_photo") from None
    return {"data": data, "mime_type": mime}, {
        "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
        "mime_type": mime, "width": width, "height": height,
    }


def validate_photos(photos, zones):
    if photos is None or photos == {}:
        return {}, {}
    if not isinstance(photos, dict) or set(photos) != set(zones):
        raise PhotoError("photos_incomplete")
    payload, metadata = {}, {}
    for zone, photo in photos.items():
        payload[zone], metadata[zone] = validate_photo(photo)
    return payload, metadata
