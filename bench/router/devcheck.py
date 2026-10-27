import glob
import os
import sys


def handles(pid="self"):
    out = set()
    for f in glob.glob(f"/proc/{pid}/fd/*"):
        try:
            link = os.readlink(f)
        except OSError:
            continue
        if "/dev/dri" in link or "nvidia" in link:
            out.add(link)
    return sorted(out)


if __name__ == "__main__":
    print("device handles of", sys.argv[1], handles(sys.argv[1]), flush=True)
