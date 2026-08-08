"""Load Candidate 16 v2 router tests from compressed source."""
from pathlib import Path
import zlib

_payload = Path(__file__).with_name("test_accepted_failure_router.py.zlib")
_source = zlib.decompress(_payload.read_bytes()).decode("utf-8")
exec(compile(_source, str(_payload) + "::source", "exec"), globals(), globals())
