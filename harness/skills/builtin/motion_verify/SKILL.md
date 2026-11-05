---
name: motion_verify
description: Shared deliver-and-verify toolkit for every built-in motion skill — freeze a frame with seek-shot.sh, tile frames into a contact sheet with contact-sheet.sh, and assert the encoded MP4's real resolution/codec/fps/duration with probe-mp4.sh. Load this when a motion skill tells you to run scripts/contact-sheet.sh, scripts/probe-mp4.sh, or scripts/seek-shot.sh, or when you need to inspect your own render before delivering it.
triggers: ["contact sheet", "probe mp4", "seek shot", "verify render", "ffprobe", "frame capture", "deliver and verify"]
---
# Motion Verify Toolkit for Harness

The built-in motion skills ship this one shared helper directory. Upstream, each iart-ai
pack carried its own copy at the pack root; vendored into Harness they all resolve here.

## Location

```
harness/skills/builtin/motion_verify/scripts/
├── seek-shot.sh        # freeze moments of a standalone HTML page (?t=N) and screenshot each
├── contact-sheet.sh    # tile frames side-by-side (start | mid | end) into one image
├── probe-mp4.sh        # print + assert an MP4's real resolution / codec / fps / duration
└── README.md           # upstream usage notes, requirements, per-tier recipes
```

## Usage by tier

**Light — standalone HTML** (`?t=N` seek harness): freeze, shoot, tile.

```bash
SCRIPTS=<path>/harness/skills/builtin/motion_verify/scripts
"$SCRIPTS"/seek-shot.sh anim.html 0 1.5 3        # → frame-0.png frame-1.5.png frame-3.png
"$SCRIPTS"/contact-sheet.sh sheet.png frame-*.png # inspect: hook reads? text clipped? loop seam matches?
```

**Heavy — Remotion**: render stills with the *real* shipped props, tile, then assert the MP4.

```bash
npx remotion still Short out/f-hook.png --frame=10 --props='{...}'
npx remotion still Short out/f-mid.png  --frame=N  --props='{...}'
npx remotion still Short out/f-end.png  --frame=L  --props='{...}'
"$SCRIPTS"/contact-sheet.sh sheet.png out/f-hook.png out/f-mid.png out/f-end.png
npx remotion render Short out/short.mp4 --props='{...}'
"$SCRIPTS"/probe-mp4.sh out/short.mp4 1080x1920 30      # vertical short contract
```

**Heavy — Manim**: save the last frame (`-s`) / a range (`-n a,b`), tile, assert.

```bash
manim -s -ql scene.py MyScene                        # media/images/.../MyScene.png
"$SCRIPTS"/probe-mp4.sh media/videos/scene/480p15/MyScene.mp4
```

## Requirements

- `seek-shot.sh` → `npx` + Playwright Chromium (`npx playwright install chromium` once).
- `contact-sheet.sh` / `probe-mp4.sh` → `ffmpeg` / `ffprobe`.

All three are POSIX `bash` and use `set -euo pipefail`. Run them from the project where
the deliverable lives; inputs and outputs are relative to that working directory.

## Why this matters

Renders lie; the file doesn't. `probe-mp4.sh` reads the actual encoded stream, so
"1080x1920 @30 h264" is *verified*, not assumed. `contact-sheet.sh` exists because seeing
start | mid | end in one image beats flipping between three when checking a hook, a loop
seam, or clipped text.

## Attribution

Scripts are vendored verbatim from [iart-ai/motion-skills](https://github.com/iart-ai/motion-skills)
(`tools/verify/`), MIT © 2026 iart.ai.