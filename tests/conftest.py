import os
import shutil
from pathlib import Path

import pytest

from diff2dict.unicodetools import count_graphemes, read_lines, suggest_class

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample"


# Let tests find TECkit binaries that are not on PATH. This runs at import,
# before the skip condition below is evaluated.
if os.environ.get("TECKIT_DIR"):
    os.environ["PATH"] = os.environ["TECKIT_DIR"] + os.pathsep + os.environ["PATH"]


@pytest.fixture
def sample_lines():
    return read_lines(SAMPLE / "source.txt"), read_lines(SAMPLE / "target.txt")


@pytest.fixture
def sample_classes(sample_lines):
    src, tgt = sample_lines
    return {g: suggest_class(g) for g in count_graphemes(src + tgt)}


teckit_missing = pytest.mark.skipif(
    not (shutil.which("teckit_compile") and shutil.which("txtconv")),
    reason="TECkit tools not on PATH (set TECKIT_DIR to their folder)")
