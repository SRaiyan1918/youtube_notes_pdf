import glob
import os
import shutil
import subprocess
import sys

import cv2
import img2pdf
import numpy as np

URL = sys.argv[1]
START_MIN = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
DUR_MIN = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
HEIGHT = int(sys.argv[4]) if len(sys.argv) > 4 else 720

# ---- Tuning (pehli guess, scores.csv dekhkar badal sakte hain) ----
SAMPLE_SEC = 2      # har 2 second pe ek frame
BLOCK_THR = 8       # ek block itna badle to "badla hua" maana jayega
CUT_SCORE = 0.30    # itne % blocks badle to slide-change ka shak
SAME_SCORE = 0.10   # isse kam fark = same slide (dedupe)
MIN_FRAMES = 3      # isse chhote segments ignore

for d in ("out/pages", "tmp/frames"):
    os.makedirs(d, exist_ok=True)
report = []


def log(msg):
    print(msg, flush=True)
    report.append(msg)


def finish(ok):
    with open("out/report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    sys.exit(0 if ok else 1)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


def stamp(i):
    s = int(START_MIN * 60 + i * SAMPLE_SEC)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ---------------- STEP 1: download ----------------
YT = ["yt-dlp", "--no-playlist", "--js-runtimes", "node",
      "--remote-components", "ejs:github"]
cookies = os.environ.get("YT_COOKIES", "").strip()
if cookies:
    with open("tmp/cookies.txt", "w", encoding="utf-8") as f:
        f.write(cookies + "\n")
    YT += ["--cookies", "tmp/cookies.txt"]

code, out = run(
    YT + ["-f", f"bv*[height<={HEIGHT}]/b[height<={HEIGHT}]",
          "--retries", "5", "--socket-timeout", "30",
          "-o", "tmp/full.%(ext)s", URL]
)
full = glob.glob("tmp/full.*")
if code != 0 or not full:
    log("STEP 1 FAIL: download nahi hua")
    log(out[-1500:])
    finish(False)
log(f"STEP 1 PASS: video download ({os.path.getsize(full[0]) / 1e6:.0f} MB)")

# ---------------- STEP 2: frames ----------------
code, out = run(
    ["ffmpeg", "-y", "-ss", str(START_MIN * 60), "-t", str(DUR_MIN * 60),
     "-i", full[0],
     "-vf", f"fps=1/{SAMPLE_SEC},scale=-2:'min(ih,{HEIGHT})'",
     "-q:v", "3", "tmp/frames/f_%05d.jpg"]
)
files = sorted(glob.glob("tmp/frames/*.jpg"))
n = len(files)
if code != 0 or n < 5:
    log("STEP 2 FAIL: frames nahi nikle")
    log(out[-1500:])
    finish(False)
log(f"STEP 2 PASS: {n} frames ({DUR_MIN:g} min, har {SAMPLE_SEC}s)")
os.remove(full[0])


# ---------------- STEP 3: slide change detection ----------------
def small(path):
    g = cv2.imread(path, cv2.IMREAD_REDUCED_GRAYSCALE_4)
    g = cv2.resize(g, (160, 90), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(g, (5, 5), 0)


def score(a, b):
    d = cv2.absdiff(a, b)
    blocks = cv2.resize(d, (16, 9), interpolation=cv2.INTER_AREA)
    return float((blocks > BLOCK_THR).mean())


smalls = [small(p) for p in files]
seq = [0.0] + [score(smalls[i - 1], smalls[i]) for i in range(1, n)]

with open("out/scores.csv", "w", encoding="utf-8") as f:
    f.write("frame,time,score\n")
    for i, s in enumerate(seq):
        f.write(f"{i},{stamp(i)},{s:.3f}\n")

cuts = [0]
for i in range(1, n):
    if seq[i] >= CUT_SCORE:
        nxt = smalls[i + 1] if i + 1 < n else smalls[i]
        # teacher kuch der ke liye guzre to cut nahi; badlav tikna chahiye
        if score(smalls[i - 1], nxt) >= CUT_SCORE:
            cuts.append(i)

segments = []
for k, a in enumerate(cuts):
    b = cuts[k + 1] - 1 if k + 1 < len(cuts) else n - 1
    if b - a + 1 >= MIN_FRAMES:
        segments.append((a, b))
log(f"STEP 3: {len(cuts)} cuts mile, {len(segments)} segments bane")


# ---------------- STEP 4: har segment ka best frame ----------------
def pick(a, b):
    idx = list(range(a, b + 1))
    if len(idx) > 3:
        idx = idx[:-1]  # aakhri frame transition ho sakta hai
    tail = idx[-max(3, int(len(idx) * 0.4)):]  # likhawat aakhir mein poori hoti hai
    if len(tail) > 25:
        tail = [tail[j] for j in np.linspace(0, len(tail) - 1, 25).astype(int)]
    stack = np.stack([smalls[j] for j in tail]).astype(np.float32)
    med = np.median(stack, axis=0)
    # median = lagbhag teacher-free background; uske sabse kareeb frame chuno
    dists = [float(np.abs(smalls[j].astype(np.float32) - med).mean()) for j in tail]
    return tail[int(np.argmin(dists))]


pages = []
for a, b in segments:
    rep = pick(a, b)
    if pages and score(smalls[pages[-1][2]], smalls[rep]) < SAME_SCORE:
        pages[-1] = (pages[-1][0], b, rep)  # duplicate: baad wala (zyada poora) rakho
        continue
    pages.append((a, b, rep))

paths = []
for k, (a, b, rep) in enumerate(pages, 1):
    dst = f"out/pages/page_{k:03d}.jpg"
    shutil.copy(files[rep], dst)
    paths.append(dst)
    log(f"page {k:03d}: {stamp(a)} - {stamp(b)}  (frame chuna: {stamp(rep)})")

if not paths:
    log("FAIL: koi slide nahi mili")
    finish(False)

# ---------------- STEP 5: PDF ----------------
with open("out/lecture_slides.pdf", "wb") as f:
    f.write(img2pdf.convert(paths))
size_mb = os.path.getsize("out/lecture_slides.pdf") / 1e6
log(f"STEP 5 PASS: {len(paths)} pages, PDF {size_mb:.1f} MB")
finish(True)
