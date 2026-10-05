import hashlib
import os
import re

import main
from conftest import ROOT


def test_every_part_matches_the_loader_checksums():
    text = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    parts = re.findall(r'\("(part\d+\.py)", (\d+), "([0-9a-f ]+)"\)', text)
    assert parts, "main.py lists no parts"
    for name, count, sums in parts:
        rows = [line.rstrip() for line in open(os.path.join(ROOT, name), encoding="utf-8").read().splitlines() if line.strip()]
        assert len(rows) == int(count), name
        got = " ".join(hashlib.sha1("\n".join(rows[k:k + 20]).encode()).hexdigest()[:6] for k in range(0, len(rows), 20))
        assert got == sums, name


def test_mcp_part_loads_last():
    text = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert re.findall(r'\("(part\d+)\.py"', text)[-1] == "part7"


def test_app_is_built():
    assert main.app.title == "TVibex402"
    assert main.app.version == main.VERSION
