import hashlib
from pathlib import Path

import rapidjson

json_load = rapidjson.Decoder(
    parse_mode=rapidjson.PM_COMMENTS | rapidjson.PM_TRAILING_COMMAS
)

def file_digest(algorithm: str, path: Path, /) -> str:
    """Output hex string is always in lower case."""
    ONE_MIB = 1 << 20
    hasher = hashlib.new(algorithm)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(ONE_MIB), b""):
            hasher.update(chunk)
    return hasher.hexdigest().lower()