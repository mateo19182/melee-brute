"""Which source lines the validation recordings execute.

`make validation-coverage` builds the native runner with coverage counters, plays the aggregate through
it and calls this on the build directory. The report says how much of each owner the gate can vouch
for: code no recording reaches is not checked by the gate at any optimization level.
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
from pathlib import Path


def collect(root: Path, objects: list[Path], files: dict[str, dict] | None = None) -> dict[str, dict]:
    """Per source file under src/: line -> count and function -> count, the most over every object."""
    files = {} if files is None else files
    for counts in sorted(c for directory in objects for c in directory.rglob("*.gcda")):
        out = subprocess.run(["gcov", "--json-format", "--stdout", counts.name], capture_output=True,
                             cwd=counts.parent).stdout
        for line in out.splitlines():
            for record in json.loads(line).get("files", []):
                path = (root / record["file"]).resolve()
                if not path.is_relative_to(root / "src") or path.suffix != ".c":
                    continue
                entry = files.setdefault(str(path.relative_to(root / "src")), {"lines": {}, "functions": {}})
                for row in record["lines"]:
                    n = row["line_number"]
                    entry["lines"][n] = max(entry["lines"].get(n, 0), row["count"])
                for row in record["functions"]:
                    name = row["name"]
                    entry["functions"][name] = max(entry["functions"].get(name, 0), row["execution_count"])
    return files


def summary(files: dict[str, dict]) -> dict[str, dict]:
    return {name: {"lines": len(f["lines"]), "lines_hit": sum(1 for c in f["lines"].values() if c),
                   "functions": len(f["functions"]),
                   "functions_hit": sum(1 for c in f["functions"].values() if c),
                   "functions_missed": sorted(n for n, c in f["functions"].items() if not c)}
            for name, f in sorted(files.items())}


def owner(name: str) -> str:
    parts = name.split("/")
    if name.startswith("melee/ft/chara/"):
        return "/".join(parts[:4])
    return "/".join(parts[:2]) if parts[0] in ("melee", "sysdolphin") else parts[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("objects", type=Path, nargs="+", help="object directories of coverage builds, added together")
    ap.add_argument("--json", type=Path, help="write the per-file summary, with the functions never called")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    files = summary(collect(root, [d.resolve() for d in args.objects]))
    if args.json:
        args.json.write_text(json.dumps(files, indent=1) + "\n")
    owners: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0, 0])
    for name, f in files.items():
        for key in (owner(name), "all"):
            row = owners[key]
            row[0] += f["lines"]
            row[1] += f["lines_hit"]
            row[2] += f["functions"]
            row[3] += f["functions_hit"]
    print(f"{'owner':34s} {'lines':>15s} {'':>6s} {'functions':>13s}")
    for key, (lines, hit, functions, called) in sorted(owners.items(), key=lambda kv: kv[1][1] / max(kv[1][0], 1)):
        if lines >= 100:
            print(f"{key:34s} {hit:7d}/{lines:7d} {100 * hit / lines:5.1f}% {called:6d}/{functions:6d} "
                  f"{100 * called / max(functions, 1):5.1f}%")


if __name__ == "__main__":
    main()
