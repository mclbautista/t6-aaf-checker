"""Field-recorder metadata observations and per-clip warnings."""

import struct
from xml.etree import ElementTree


class IXMLReader:
    """Parse just the direct BWFXML fields incrementally, discarding XML nodes."""

    def __init__(self):
        self.parser = ElementTree.XMLPullParser(events=("start", "end"))
        self.stack = []
        self.values = {}
        self.invalid = False

    def feed(self, block):
        if self.invalid:
            return
        try:
            self.parser.feed(block)
            for event, element in self.parser.read_events():
                if event == "start":
                    self.stack.append(element.tag)
                else:
                    if len(self.stack) == 2 and self.stack[0] == "BWFXML":
                        self.values[element.tag] = element.text or ""
                    self.stack.pop()
                    element.clear()
        except ElementTree.ParseError:
            self.invalid = True

    def finish(self):
        try:
            self.parser.close()
        except ElementTree.ParseError:
            self.invalid = True
        return {} if self.invalid else self.values


def linked_ixml(path, chunk_bytes):
    """Seek over WAV payloads; read only RIFF headers and iXML in bounded blocks."""
    try:
        with path.open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
                return {}
            end = 8 + struct.unpack_from("<I", header, 4)[0]
            while stream.tell() + 8 <= end:
                header = stream.read(8)
                if len(header) != 8:
                    return {}
                chunk_id, size = struct.unpack("<4sI", header)
                if stream.tell() + size > end:
                    return {}
                if chunk_id == b"iXML":
                    parser = IXMLReader()
                    while size:
                        block = stream.read(min(size, chunk_bytes))
                        if not block:
                            return {}
                        size -= len(block)
                        parser.feed(block)
                    return parser.finish()
                stream.seek(size + size % 2, 1)
    except OSError:
        pass
    return {}


def linked_chunk_ids(path):
    """RIFF chunk IDs of a linked WAV; headers only, payloads are seeked over."""
    ids = set()
    try:
        with path.open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
                return ids
            end = 8 + struct.unpack_from("<I", header, 4)[0]
            while stream.tell() + 8 <= end:
                header = stream.read(8)
                if len(header) != 8:
                    break
                chunk_id, size = struct.unpack("<4sI", header)
                ids.add(chunk_id)
                stream.seek(size + size % 2, 1)
    except OSError:
        pass
    return ids


def metadata_values(sources, ixml, field_map):
    sources = {**sources, "ixml": ixml}
    values = {}
    for field, locations in field_map.items():
        for location in locations:
            source, key = location["source"], location["key"]
            if source == "master_mob_comment":
                key = key.casefold()
            value = sources.get(source, {}).get(key)
            if value is not None and str(value).strip():
                values[field] = str(value).strip()
                break
    return values


def missing_metadata_message(values, rule):
    blank = [field for field in rule["field_map"] if not values.get(field, "").strip()]
    return rule["message"].format(fields=", ".join(blank)) if blank else None
