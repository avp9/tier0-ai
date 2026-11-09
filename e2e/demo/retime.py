#!/usr/bin/env python3
"""Cut the dull part out of the feature tour and fit it to a length.

  python3 demo/retime.py <raw.webm> <waits.json> <out.mp4> [target_seconds]

waits.json lists [start, end] seconds of each wait for a model (written by feature-tour.spec.ts).
Only the first moments of each long wait are kept (the "is typing" part) and its last quarter
second. With a target length, the longest first part that still fits is chosen (between 0.4 s
and 4 s), so a slow day and a fast day give the same video length.
Needs ffmpeg with filters: `pip install imageio-ffmpeg` (a static ffmpeg, nothing system-wide)
or ffmpeg on the PATH.
"""
import json
import shutil
import subprocess
import sys

raw, waits_file, out = sys.argv[1], sys.argv[2], sys.argv[3]
target = float(sys.argv[4]) if len(sys.argv) > 4 else None
tail = 0.25
try:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    ffmpeg = shutil.which("ffmpeg")
if not ffmpeg:
    sys.exit("ffmpeg not found: pip install imageio-ffmpeg")

probe = subprocess.run([ffmpeg, "-i", raw], capture_output=True, text=True).stderr
duration = None
for line in probe.splitlines():
    if "Duration:" in line:
        h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
        duration = int(h) * 3600 + int(m) * 60 + float(s)
if duration is None:
    sys.exit("could not read the video length")

waits = json.load(open(waits_file))


def keep_ranges(head: float) -> list[tuple[float, float]]:
    """Everything except the middle of each long wait."""
    cuts = sorted((a + head, b - tail) for a, b in waits if b - a > head + tail + 0.3)
    keeps, cursor = [], 0.0
    for a, b in cuts:
        keeps.append((cursor, a))
        cursor = b
    keeps.append((cursor, duration))
    return keeps


def length(head: float) -> float:
    return sum(b - a for a, b in keep_ranges(head))


head = 1.2
if target is not None:
    low, high = 0.4, 4.0
    if length(low) > target:
        print(f"warning: even the shortest cut is {length(low):.1f}s, longer than {target}s", file=sys.stderr)
        head = low
    else:
        for _ in range(30):
            mid = (low + high) / 2
            low, high = (mid, high) if length(mid) <= target else (low, mid)
        head = low
keeps = keep_ranges(head)
parts = [
    f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]" for i, (a, b) in enumerate(keeps)
]
chain = "".join(f"[v{i}]" for i in range(len(keeps)))
graph = ";".join(parts) + f";{chain}concat=n={len(keeps)}:v=1:a=0,format=yuv420p[out]"
subprocess.run(
    [ffmpeg, "-y", "-loglevel", "error", "-i", raw, "-filter_complex", graph, "-map", "[out]",
     "-c:v", "libx264", "-crf", "23", "-preset", "medium", "-movflags", "+faststart", out],
    check=True,
)
final = sum(b - a for a, b in keeps)
print(f"{duration:.1f}s -> {final:.1f}s, keeping {head:.2f}s of each long wait ({out})")
