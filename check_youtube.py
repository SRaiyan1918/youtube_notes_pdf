import glob
import os
import subprocess
import sys

from PIL import Image

url = sys.argv[1]
start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
dur = int(sys.argv[3]) if len(sys.argv) > 3 else 60

os.makedirs("out/frames", exist_ok=True)
os.makedirs("tmp", exist_ok=True)
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


YT = ["yt-dlp", "--no-playlist", "--js-runtimes", "node",
      "--remote-components", "ejs:github"]

cookies = os.environ.get("YT_COOKIES", "").strip()
if cookies:
    with open("tmp/cookies.txt", "w", encoding="utf-8") as f:
        f.write(cookies + "\n")
    YT += ["--cookies", "tmp/cookies.txt"]
    log("INFO: cookies use ho rahi hain")
else:
    log("INFO: bina cookies ke try ho raha hai")

# STEP 1: metadata
code, out = run(YT + ["--print", "%(title)s | %(duration)s sec", url])
if code != 0:
    log("STEP 1 FAIL: YouTube ne metadata nahi diya (bot-block ya galat URL)")
    log(out[-1500:])
    finish(False)
log("STEP 1 PASS: metadata mil gaya -> " + out.strip().splitlines()[-1])

# STEP 2: sirf chhota hissa download (video only, max 720p)
section = f"*{start}-{start + dur}"
code, out = run(
    YT
    + ["-f", "bv*[height<=720]/b[height<=720]", "--download-sections", section,
       "--force-keyframes-at-cuts", "-o", "tmp/video.%(ext)s", url]
)
files = glob.glob("tmp/video.*")
if code != 0 or not files:
    log("STEP 2 FAIL: video download block ya fail hua")
    log(out[-1500:])
    finish(False)
size_mb = os.path.getsize(files[0]) / 1e6
log(f"STEP 2 PASS: {dur}s ka video download hua ({size_mb:.1f} MB)")

# STEP 3: har 10 second pe ek frame (slide jaisa)
code, out = run(
    ["ffmpeg", "-y", "-i", files[0], "-vf", "fps=1/10", "-q:v", "2",
     "out/frames/slide_%03d.jpg"]
)
frames = sorted(glob.glob("out/frames/*.jpg"))
if code != 0 or not frames:
    log("STEP 3 FAIL: frames nikle nahi")
    log(out[-1500:])
    finish(False)
log(f"STEP 3 PASS: {len(frames)} frames nikle")

# STEP 4: demo PDF
imgs = [Image.open(p).convert("RGB") for p in frames]
imgs[0].save("out/demo_slides.pdf", save_all=True, append_images=imgs[1:])
log("STEP 4 PASS: out/demo_slides.pdf ban gaya")
log("RESULT: YouTube GitHub Actions se kaam kar raha hai")
finish(True)
