"""TVibex402 loader.

The code lives in small files (part1.py ... part10.py) so it is easy to copy on a phone.
This file runs them in order inside ONE shared namespace, so it behaves like a single big file.
part7.py (the MCP server) must stay LAST.

Each part is checked before it runs. Blank lines and trailing spaces do not matter. If the copy was cut,
or one line is different, the error says which part and which line numbers to re-check.
"""
import hashlib
import os

# (file, number of non-blank lines, short checksum of every block of 20 non-blank lines)
_LOADER_PARTS = [
    ("part1.py", 188, "e743de c1ac12 601c39 4d9d2f 4a4aec 05b54d 325cc3 3ebd7b 2a67a4 505939"),
    ("part2.py", 284, "dd2adb 5c8bc2 579e5d 5ab3be 89399b 1d9ab1 e238d9 dfeb14 3f87da 3d1956 c72169 9d2495 282293 beb4a9 a9f93e"),
    ("part3.py", 277, "47f898 026ec3 d51ae5 17295c 3833d1 bb6952 1bdf56 bb6399 6a0e6c 492db2 75f857 881805 7a892c efed49"),
    ("part4.py", 193, "3ba2c3 f8144d 009b88 6871a4 4598d2 4a8aa6 cba613 3a0bb7 bb97b3 6f9c9b"),
    ("part5.py", 259, "1383cd 624af2 e42df9 b0a03d 5093d2 ff2cf4 dd3f20 de31db 68ab34 554169 6d09d7 287100 124e0f"),
    ("part6.py", 265, "7bfb4f c5b492 560dc6 071b1d a28aee 2012e2 4ba7b7 caa55c a1b6d2 ea3935 dfddfe 5ad147 fd85cc b73b36"),
    ("part8.py", 230, "aa054f b95c6b 2c42db 9f4cdf 3bed1d bc282a 01499a 749f79 aeb83d 2d4e7b 619823 6cd0af"),
    ("part9.py", 141, "4ef5cd ab83e5 a63d4e 9a89b6 da830f ad37b9 5f3244 17b09e"),
    ("part10.py", 100, "2bef81 f88fc8 a5da65 7fe0e9 9d8d7a"),
    ("part7.py", 123, "3cb58c 4f50d8 5f4f96 8d1647 de3ce3 fe58bb a57cce"),
]


def _load_parts():  # a function, so the loader's own variables never leak into the app's namespace
    def checksum(block):
        return hashlib.sha1("\n".join(text for _, text in block).encode()).hexdigest()[:6]

    def first_difference(rows, sums):
        want = sums.split()
        for k in range(0, len(rows), 20):
            block = rows[k:k + 20]
            if k // 20 >= len(want):
                return f"There is extra text after line {block[0][0]}."
            if checksum(block) != want[k // 20]:
                return (f"The first difference is between line {block[0][0]} and line {block[-1][0]} "
                        f"(it starts with: {block[0][1].strip()[:50]!r}). Look for a line that was split in two, "
                        f"repeated, or added there.")
        return "The start matches; the end of the file is missing or has extra lines."

    folder = os.path.dirname(os.path.abspath(__file__))
    for name, expected, sums in _LOADER_PARTS:
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            raise RuntimeError(f"{name} is missing: upload all {len(_LOADER_PARTS)} part files next to main.py")
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
        rows = [(i + 1, line.rstrip()) for i, line in enumerate(text.splitlines()) if line.strip()]
        if len(rows) != expected:
            raise RuntimeError(f"{name} has {len(rows)} non-blank lines but should have {expected}. "
                               f"{first_difference(rows, sums)}")
        want = sums.split()
        for k in range(0, len(rows), 20):
            block = rows[k:k + 20]
            if checksum(block) != want[k // 20]:
                raise RuntimeError(f"{name}: the text differs from the original between line {block[0][0]} and line "
                                   f"{block[-1][0]} (it starts with: {block[0][1].strip()[:50]!r}). Re-copy that part")
        exec(compile(text, path, "exec"), globals())


_load_parts()
del _load_parts
