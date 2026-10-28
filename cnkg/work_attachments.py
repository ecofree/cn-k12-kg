"""Small, owner-scoped process artifacts saved with immutable work revisions.

Images and audio await review. Their presence or matching checksum never judges
a drawing, proof or spoken explanation. Filenames never become file paths.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import re

MAX_FILE_BYTES = 512_000
MAX_TOTAL_BYTES = 768_000
MAX_ATTACHMENTS = 6
MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "audio/wav", "audio/webm", "audio/ogg", "audio/mp4"})


def _matches_type(raw, media_type):
    if media_type == "image/png":
        return len(raw) >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR"
    if media_type == "image/jpeg":
        return len(raw) >= 4 and raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9")
    if media_type in {"image/webp", "audio/wav"}:
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == (b"WEBP" if media_type == "image/webp" else b"WAVE")
    if media_type == "audio/ogg":
        return raw.startswith(b"OggS")
    if media_type == "audio/webm":
        return raw.startswith(b"\x1a\x45\xdf\xa3")
    if media_type == "audio/mp4":
        return len(raw) >= 12 and raw[4:8] == b"ftyp"
    return False


def validate_attachments(values, field_ids):
    if not isinstance(values, list) or len(values) > MAX_ATTACHMENTS:
        raise ValueError("每份作品最多保存6个图稿或录音文件")
    total, seen = 0, set()
    required = {"field_id", "filename", "media_type", "byte_size", "sha256", "data_base64"}
    for item in values:
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("作品附件格式不完整")
        if not isinstance(item["field_id"], str) or item["field_id"] not in field_ids:
            raise ValueError("附件必须对应这份作品中的具体过程项")
        name = item["filename"]
        if not isinstance(name, str) or not 1 <= len(name) <= 160 or re.search(r"[\x00-\x1f\x7f/\\]", name):
            raise ValueError("附件名称须为不含路径的普通文件名")
        media_type = item["media_type"]
        if not isinstance(media_type, str) or media_type not in MIME_TYPES:
            raise ValueError("附件仅支持PNG、JPEG、WebP图稿和WAV、WebM、Ogg、MP4录音")
        encoded, size = item["data_base64"], item["byte_size"]
        if (not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3)
                or isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_FILE_BYTES):
            raise ValueError("单个附件须不超过500 KB")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("附件内容编码不正确") from None
        if len(raw) != size or not _matches_type(raw, media_type):
            raise ValueError("附件的实际内容与文件类型或长度不一致")
        digest = hashlib.sha256(raw).hexdigest()
        if item["sha256"] != digest:
            raise ValueError("附件内容指纹不一致，请重新选择原文件")
        key = item["field_id"], digest
        if key in seen:
            raise ValueError("同一过程项不能重复添加相同附件")
        seen.add(key)
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("每份作品的图稿与录音合计须不超过750 KB")
    return copy.deepcopy(values)
