"""TVibex402 loader.

The code lives in part1.py ... part7.py (small files, easy to copy on a phone).
This file runs them in order inside ONE shared namespace, so it behaves exactly like a single big file.
If a part is missing or was cut while copying, the error says which one.
"""
import os

_LOADER_PARTS = [
    ("part1.py", 228),
    ("part2.py", 313),
    ("part3.py", 315),
    ("part4.py", 214),
    ("part5.py", 286),
    ("part6.py", 294),
    ("part7.py", 143),
]


def _load_parts():  # a function, so the loader's own variables never leak into the app's namespace
    folder = os.path.dirname(os.path.abspath(__file__))
    for name, expected in _LOADER_PARTS:
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            raise RuntimeError(f"{name} is missing: upload all {len(_LOADER_PARTS)} part files next to main.py")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        got = len(text.rstrip().splitlines())
        if got != expected:
            raise RuntimeError(f"{name} has {got} lines but should have {expected}: the copy was cut or has extra lines, copy it again")
        exec(compile(text, path, "exec"), globals())


_load_parts()
del _load_parts
