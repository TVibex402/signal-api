"""TVibex402 loader.

The code lives in small files (part1.py ... part30.py) so it is easy to copy on a phone.
This file runs them in order inside ONE shared namespace, so it behaves like a single big file.
part7.py (the MCP server) must stay LAST.

Each part is checked before it runs. Blank lines and trailing spaces do not matter. If the copy was cut,
or one line is different, the error says which part and which line numbers to re-check.
"""
import hashlib
import os

# (file, number of non-blank lines, short checksum of every block of 20 non-blank lines)
# A checksum of "SKIP" means: run the file without checking it.
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
    ("part11.py", 176, "d86a06 92af86 39ff8f 75bf06 5ad6e4 d4f2f7 de4ad1 7cb671 ef4bf7"),
    ("part12.py", 92, "7f4810 568ec7 9e61a6 78d451 576456"),
    ("part13.py", 287, "78d716 efc00a 12ff47 0acd55 a85d72 46255a 596e6c 8afc61 4e630f 96df20 e98fd8 bcd912 f83a8a 9c6ad6 f76e00"),
    ("part14.py", 114, "df8203 0eb4a5 47bf27 afb8c1 2287d7 fa0444"),
    ("part15.py", 198, "2340e5 c1ec7b e87bd8 3d110d abae99 8ef8c5 88aa14 3b397f f6b7ca 5af43d"),
    ("part16.py", 89, "6c30d7 15b149 84b88b 57bfc7 47ed07"),
    ("part17.py", 131, "66d57d 9d984d 1ccb46 2da000 2e320e 5cea66 3b156c"),
    ("part18.py", 156, "9ea14e c549b7 61d859 e91ee5 1f3dff 1d58ee b63cdd ed71c2"),
    ("part19.py", 432, "208858 5629ae 34970a c12d09 3d6b7f 5ed271 139db2 236d2c c42389 610935 010a60 de9b67 6ffe34 e2114f 5118bb 708e99 a7b490 4e4014 eab1a9 fefe2d f62c54 c04537"),
    ("part20.py", 99, "b7b12c e102a7 407654 ab8815 5fb223"),
    ("part21.py", 188, "45d1ae e2ac9d 3e48f5 ae7375 448d44 b81e1a a6eaa5 ab8815 7f44a0 823cb9"),
    ("part22.py", 241, "3bff8c 9ba5ed fb77ff 84eddf 0c0c2a 4bfba0 651917 190044 b24cc5 35dcdc 9a0364 0b58a8 ca9550"),
    ("part23.py", 137, "729707 2ee03d 21b402 ab4caa 65c187 39cc89 3d856f"),
    ("part24.py", 300, "8c365c 82be87 d2de0f 95f6f6 832b3e c1767b d7314c 00f872 bc4339 3acefb d63b49 8f7ece d4e700 3f111c 7f6b35"),
    ("part25.py", 119, "1ede2b 7885ba 23e645 e72294 dd0e51 266990"),
    ("part26.py", 186, "1ad838 c839fa 5446e1 14224a 3dcf78 a169ea 11442d 7e7124 73472d 56c4fa"),
    ("part27.py", 92, "c95253 f3cdab a4d8c9 e1a219 a7a9d1"),
    ("part28.py", 310, "cbb9c4 c1bb76 a7d79c 1bd50e 5dbcd7 869b5e 35cbf6 167ddf 6f930a c984f9 e870fe f2af5c eeac36 ad14bf 9abf74 66be11"),
    ("part29.py", 27, "d18441 9569d7"),
    ("part30.py", 68, "3500d5 c11459 937a5d ff8e38"),
    ("part7.py", 127, "3cb58c dc8bcc 5f4f96 8d1647 05d9f8 f88499 86b940"),
]


def _load_parts():
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
            raise RuntimeError(f"{name} is missing: upload all part files next to main.py")
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
        rows = [(i + 1, line.rstrip()) for i, line in enumerate(text.splitlines()) if line.strip()]

        if sums == "SKIP":
            print(f"[loader] {name}: not checked ({len(rows)} non-blank lines)")
            exec(compile(text, path, "exec"), globals())
            continue

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
