import os
import tempfile

# Keep tests away from the real data dir / token.
os.environ["EM_DATA_DIR"] = tempfile.mkdtemp(prefix="em-test-")
os.environ.pop("EM_TOKEN", None)
