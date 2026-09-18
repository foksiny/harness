---
name: motion_graphics
description: Professional motion graphics creation — concept to final render. Covers animation principles, timing/easing, kinetic typography, composition, code-based motion (Remotion/GSAP/Lottie/Manim), After Effects workflows, and rigorous anti-slop QA to prevent amateur results.
triggers: [motion graphics, motion design, animation, animated, video, explainer, title sequence, intro, outro, kinetic typography, lottie, gsap, remotion, manim, after effects, ae, premiere, blender animation, framer motion, svg animation, canvas animation, lower third, logo animation, transition, stinger]
---
# Motion Graphics for Harness — Professional Production Standard

> **Purpose:** Turn any motion graphics brief into a broadcast/client-ready deliverable. This skill enforces a **zero-slop** standard: no jank, no popping, no inconsistent easing, no unmotivated motion, no compression-banded gradients, no off-brand typography. Every sequence must pass the QA gate before delivery.

---

## 0) When to Activate This Skill

Activate for: explainer videos, logo animations, title sequences/intros/outros, lower-thirds, kinetic typography, product demos, data-visualization animations, Lottie/web animations, social media motion ads, slide-to-video conversions, Remotion/GSAP/Canvas/SVG/WebGL animation, Manim educational animations, After Effects comps, or any request containing "animate", "motion", "video", "transition", or "timeline".

If the user request is ambiguous ("make it move" / "add some animation"), **clarify intent before animating** using `ask_user` — do not guess duration, aspect ratio, or style.

---

## 1) Mandatory Pre-Production (No Exceptions)

**Never jump straight to keyframes.** Harness must complete this phase and show the user before production.

### 1.1 Brief Extraction (ask_user if missing)
Extract and lock:
- **Objective & Audience:** What must the viewer understand/feel/do after watching?
- **Duration & Format:** Exact seconds, aspect ratio(s) (16:9, 9:16, 1:1, 4:5), resolution (1080p/4K), FPS (24/30/60 — lock one, never mix), delivery codec
- **Brand Constraints:** Logo clear-space, palette hex, typefaces + weights, tone (e.g. "minimal / corporate / playful / cinematic")
- **Audio:** Voiceover? Music BPM? SFX needed? Subtitle requirement?
- **CTA & Copy:** Final approved text verbatim — never animate placeholder lorem
- **Inspiration:** 2–3 reference links if provided; if not, propose a mood direction and confirm

### 1.2 Concept → Style Frames → Animatic
1. **Moodboard / Style Frames:** Describe or generate 2–3 static style frames (composition, palette, type scale) before animating. Use `view_file` to inspect brand assets.
2. **Storyboard (beat sheet):** Table with columns: `Timecode | Visual | Copy/VO | Motion Intent | Audio`. Every shot gets one row. Even a 5s logo animation needs 3–5 beats.
3. **Animatic (timing pass):** Block timing with gray boxes / single-frame holds at locked FPS. Confirm beat durations sum to target duration ±0.2s before refining. No easing polish until timing is approved.

> **Anti-Slop Gate 1:** If brief extraction is incomplete, the result *will* be sloppy. Do not proceed with placeholder assumptions.

---

## 2) Professional Principles — The Non-Negotiable Foundation

### 2.1 The 12 Principles (Applied, Not Academic)
| Principle | Professional Application | Sloppy Tell |
|---|---|---|
| **Squash & Stretch** | Preserve volume; logo squash needs counter-stretch 5–12%. Overdo = jelly. | Rigid scale without deformation feels dead |
| **Anticipation** | 6–12 frame pullback before major move (e.g. text drops, pull up 8px first) | Motion appears from nowhere, no weight |
| **Staging** | One focal action per beat; use depth, scale, and blur to stage hierarchy | Competing motions confuse eye |
| **Straight Ahead vs Pose-to-Pose** | Pose-to-pose for structured UI/logo; straight-ahead only for organic particles | Inconsistent spacing on mechanical objects |
| **Follow-Through & Overlapping** | Secondary elements (shadows, badges) lag 2–6 frames, settle with decay | Everything stops on same frame = robotic |
| **Slow In / Slow Out** | Mandatory easing (never linear except mechanical/technical). See §3 | Linear = cheap, amateur |
| **Arc** | Natural motion follows arcs, not straight lines. Add 4–8% arc offset | Robotic straight-line tweens |
| **Secondary Action** | Subtle scale/perspective on primary move adds life | Flat translation only |
| **Timing** | See §3 timing bible | Even spacing = lifeless; erratic = sloppy |
| **Exaggeration** | Push 10–20% beyond realistic, then dial back until it reads on small screens | Too subtle = invisible; too much = cartoon |
| **Solid Drawing / Dimensionality** | Consistent perspective, lighting, shadow direction per scene | Shadow jumps, vanishing point shifts |
| **Appeal** | Composition, silhouette, and rhythm; every frame must work as a still | Cluttered, off-center, tangents |

### 2.2 The 7 Pillars of Anti-Slop
1. **Motivated Motion:** Every movement answers "why?" — entrance, emphasis, or transition. Decorative drift is removed.
2. **Hierarchical Timing:** Primary action longest, secondary 60–75% duration, tertiary micro-details 30–50%.
3. **Consistent Physics:** One gravity, one friction model per scene. Do not mix bounce (elastic) with smooth (cubic) arbitrarily.
4. **Pixel-Perfect Spatial:** No sub-pixel jitter, no half-pixel strokes, anchors on whole pixels (see §7.3).
5. **Typographic Integrity:** No distorted type, no widows/orphans in motion, no < 7% letter-spacing shifts during animation.
6. **Color & Light Continuity:** One light source per scene; color transitions via LAB/LCH, not RGB lerp (prevents muddy mids).
7. **Seamless Loop or Clean Out:** Loops must be mathematically seamless (first frame = last frame + 1); non-loops end on a held frame ≥12 frames.

---

## 3) Timing & Easing Bible — Exact Values

### 3.1 Duration Budgets (at 30fps unless specified)
| Motion Type | Duration | Frames @30fps | Frames @60fps | Use |
|---|---|---|---|---|
| Micro-interaction (hover, check) | 150–250ms | 5–8 | 9–15 | UI feedback |
| UI transition (panel, modal) | 300–500ms | 9–15 | 18–30 | Layout shifts |
| Text entrance (word/line) | 400–700ms | 12–21 | 24–42 | Kinetic typography |
| Logo reveal | 800–1500ms | 24–45 | 48–90 | Hero moment |
| Full scene/explainer beat | 1500–4000ms | 45–120 | 90–240 | Storytelling |
| SFX-synced hit | 80–120ms | 2–4 | 5–7 | Impact frames |

**Rule:** Never exceed 600ms for a single UI element move without a narrative reason. Never go below 100ms unless it is a deliberate flash/hit (with motion blur).

### 3.2 Easing Curves — Use These, Not Defaults
Defaults (e.g. `ease-in-out` / `easeOut`) are sloppy. Use explicit cubics:

- **Entrance (decelerate, friendly):** `cubic-bezier(0.16, 1, 0.30, 1)` — use for text/images entering. Also `easeOutExpo` approximated as `0.16,1,0.30,1`
- **Exit (accelerate, decisive):** `cubic-bezier(0.70, 0, 0.84, 0)` — use for elements leaving
- **In-Out (elegant, standard):** `cubic-bezier(0.65, 0, 0.35, 1)` — use for camera moves, large transitions
- **Emphasized (material):** `cubic-bezier(0.32, 0.72, 0, 1)` — use for hero reveals
- **Spring (playful, controlled):** `mass:1, stiffness:180, damping:18` (Framer) or `tension:200, friction:20` (GSAP). Never raw `elastic` without tuning.
- **Bounce (only for literal bounce):** `cubic-bezier(0.34, 1.56, 0.64, 1)` — cap at one bounce; more = toy-like

**Mapping by intent:**
- Corporate/minimal → `0.16,1,0.30,1` and `0.65,0,0.35,1` only
- Energetic/playful → add spring, max one per sequence
- Cinematic → `0.32,0.72,0,1` + subtle film grain/parallax
- Technical/data → `0.25,0.10,0.25,1` (linear-ish but not linear)

**Sloppy trap:** Using `linear` for organic motion, or same easing for entrance AND exit — entrance must decelerate, exit must accelerate. Mirror is wrong.

### 3.3 Stagger & Choreography
- **Stagger values:** 30–60ms between list items / letters (not 0ms, not 120ms). For 5 items, total stagger = 120–240ms.
- **Offset groups:** Never animate >3 properties simultaneously on same element (e.g. x + opacity + scale is max). Add 1–2 frame offset between properties for depth.
- **Beat choreography:** Sequence beats with 6–10 frame overlaps (new beat starts before prior settles) to avoid dead pauses, but never overlap focal actions.
- **Breathing:** After a fast move, hold 8–12 frames before next action. Without holds, motion feels rushed.

---

## 4) Composition & Visual Hierarchy in Motion

### 4.1 Grid, Safe Areas, Anchors
- **Grid:** 12-column (16:9) or 4-column (9:16) with 5% outer margin. All major elements snap to grid lines even while moving.
- **Safe areas:** Title-safe 90%, action-safe 93% (broadcast). For social, keep core message inside 80% center box (avoids cropping on feeds).
- **Anchor hygiene:** Set `transform-origin` / anchor point explicitly before animating. For text, anchor at baseline-center or cap-height center, not bounding-box center (prevents vertical wobble).
- **Rule of thirds for holds:** Final resting position should honor thirds; do not end animation at exact center unless symmetrically staged.

### 4.2 Depth & Parallax
- Fake depth with scale + blur, not just position. A 4-layer parallax (BG slow, MG mid, FG fast, overlay fastest) sells production value instantly.
- Depth order must be consistent: shadows cast one direction, blur increases with distance, scale correlates with z.
- Use `will-change: transform, opacity` sparingly and remove post-animation to avoid layer promotion leaks (web).

### 4.3 Lighting & Shadow Consistency
- One dominant light direction per scene (e.g. top-left 135°). All drop shadows, bevels, gradients respect it.
- Shadow animation: blur and offset scale with element distance (closer = sharper/darker, farther = softer/lighter). Do not animate shadow linearly — ease it with primary object at 90% duration.

---

## 5) Typography in Motion — Zero Tolerance for Slop

- **Font loading:** Preload/FOIT prevention — never animate before fonts are ready (Remotion: `delayRender` until `document.fonts.ready`; Web: `font-display: swap` + wait).
- **Kerning & Ligatures:** Animate `transform` and `opacity` only. Never animate `letter-spacing`, `word-spacing`, or `font-weight` (causes layout thrashing and reflow jitter).
- **Split strategy:** Split by word for readability; split by char/letter only for ≤ 12 characters and with `aria-label` intact for accessibility.
- **Line-length:** Max 45–60 characters per line in motion. Break lines before animating; animate lines as groups, not individual words across line breaks.
- **Scale limits:** Never scale type non-uniformly (distorts glyphs). Use `transform: scale` uniformly or switch weight.
- **Color:** Animate type color via `color` with LAB interpolation or overlay gradient; avoid animating `filter: hue-rotate` on type (fringing).
- **Read time:** Hold final type on screen ≥ (word count × 250ms) + 500ms buffer. Example: 8 words = 2.5s minimum hold. Use formula and do not cut short.
- **Widow/orphan guard:** Check final frame stills for single-word last lines; re-break before render.

---

## 6) Color, Gradients & Texture

- **Palette discipline:** Max 3 primaries + 1 accent + neutrals per piece. Animate between palette colors only — introduce new hues via accent, not random.
- **Gradient banding prevention (critical):**
  - Add 1.5–2.5% monochromatic noise/grain in compositing (After Effects: `Add Grain` 0.2; CSS/Canvas: `filter: noise` or subtle PNG overlay at 6% opacity).
  - Use 16-bit / `dither` on exports; never export 8-bit gradients without dithering.
  - For web gradients, add `background: radial-gradient(...);` with `noise` overlay div, not flat CSS gradient alone.
- **Interpolation:** Blend in `oklch`/`lab` (CSS `color-mix(in oklch, ...)`) or HCL in code. RGB lerp through muddy grays = instant amateur tell.
- **Contrast in motion:** Verify WCAG AA at motion-blurred mid-frames too — text at 60% opacity mid-transition must still be readable against BG at that frame.

---

## 7) Technical Execution — Choose the Right Stack

### 7.1 Stack Decision Tree
| Brief | Recommended Stack | Why |
|---|---|---|
| Social ad / web hero / Lottie | **GSAP + SVG** or **Lottie (Bodymovin)** or **Framer Motion** | Lightweight, scrub-able, responsive |
| Programmatic video / data-driven / bulk render | **Remotion (React)** | Code as timeline, versionable, server-render via Lambda |
| Educational / math / algorithmic | **Manim (community edition)** | Precise mathematical motion, LaTeX |
| Cinematic / VFX / character | **After Effects + ExtendScript** or **Blender** | Compositing, particles, 3D, camera |
| Real-time / interactive canvas | **Canvas/WebGL + GSAP ticker** | 60fps interactive |

Do not mix stacks arbitrarily (e.g. Remotion + manual After Effects on same project) unless the pipeline is explicit.

### 7.2 Remotion (Code Video) — Production Checklist
```tsx
// Mandatory pattern — prevents slop
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
export const MyComp: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  // 1. Physics once per scene
  const s = spring({frame, fps, config: {damping: 18, stiffness: 180, mass: 1}});
  // 2. Interpolate with explicit easing/clamp
  const opacity = interpolate(frame, [0, 12], [0, 1], {extrapolateRight: 'clamp'});
  const y = interpolate(frame, [0, 18], [24, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  // 3. Hold — do not animate through end
  const holdOpacity = frame > durationInFrames - 12 ? 1 : opacity;
  return <AbsoluteFill style={{opacity: holdOpacity, transform: `translateY(${y}px)`}} />;
};
// Export: npx remotion render src/index.ts MyComp out.mp4 --codec h264 --crf 18 --image-format jpeg
```
- **FPS lock:** Declare `fps` in `Root` and never derive from system clock.
- **Font gate:** `delayRender`/`continueRender` around `document.fonts.ready`.
- **Audio sync:** Use `useCurrentFrame` + `interpolate` tied to same `fps`; verify with `ffprobe` after render.

### 7.3 GSAP / Framer / CSS — Pixel-Perfect Rules
```js
// GSAP — correct (GPU, whole pixels, explicit ease)
gsap.fromTo(el,
  {y: 24, autoAlpha: 0},
  {y: 0, autoAlpha: 1, duration: 0.6, ease: "expo.out", overwrite: "auto",
   onUpdate: () => { // snap to pixel to prevent subpixel blur on text
     gsap.set(el, {x: Math.round(gsap.getProperty(el,"x"))});
   }}
);
// Stagger — correct range
gsap.from(".word", {yPercent: 110, duration: 0.7, ease: "expo.out", stagger: 0.04});

// Framer Motion — correct
<motion.div
  initial={{y: 24, opacity: 0}}
  animate={{y: 0, opacity: 1}}
  transition={{duration: 0.6, ease: [0.16, 1, 0.30, 1]}} // NOT spring for entrance
/>

// CSS — use transform/opacity only (never top/left/width)
.el { will-change: transform, opacity; transform: translateZ(0); }
```
- **GPU properties only:** Animate `transform` (translate/scale/rotate) + `opacity`. Never `width/height/top/left/margin/filter` on main timeline (layout thrash = jank).
- **Subpixel:** Round final positions for static text holds; keep motion subpixel during tween but snap on settle.
- **Motion blur (web):** For fast moves (> 400px/s), add 1–2 frame directional blur via GSAP `filter: blur()` at 40% midpoint, remove at settle — do not leave blur on hold frames.

### 7.4 Lottie / SVG Discipline
- Export SVG with `viewBox`, not fixed width/height; strokes with `vector-effect: non-scaling-stroke`.
- Lottie: design at 30fps, export with Bodymovin, set `renderer: 'svg'`, disable `preserveAspectRatio` surprises with `viewBox` lock.
- Path count budget: < 150 nodes per SVG frame for 60fps on mid-tier mobile. Simplify in Illustrator (`Object → Path → Simplify` 98% fidelity).
- Never animate `stroke-dasharray` for progress without rounding to whole pixels — otherwise shimmer.

### 7.5 Manim (if used)
- Use `manim.cfg` to lock `frame_rate=30`, `pixel_height=1080`, `background_color`.
- Favor `FadeIn(lag_ratio=0.1)` stagger over manual loops; group with `VGroup` before animating.
- Always call `self.wait(0.5)` after final hold before `self.play` ends — prevents abrupt cut.
- Export with `--format mp4 --quality h` and verify no LaTeX overflow beyond safe area.

### 7.6 After Effects / Blender (if used)
- Comp settings: lock FPS, shutter angle 180° (motion blur), color depth 16bpc minimum, working space sRGB or Rec.709 (declare).
- Use `Easy Ease` (F9) then Graph Editor to hit the cubics above — default easy-ease is too soft; adjust influence 65–75%.
- Pre-compose moving elements with `Continuously Rasterize` for vectors; do not scale raster above 100% (pixelation).
- Render via Media Encoder queue with explicit preset — never direct "Lossless" without codec choice.

---

## 8) Asset Pipeline — Hygiene Before Keyframes

1. **Inspect inputs:** `view_file` on every logo/SVG/brand file. Check: viewBox, embedded fonts converted to outlines, no hidden layers, no 10MB embedded bitmaps.
2. **Vector hygiene:** Ungroup single, merge overlapping paths, remove stray points (<0.5px). Run SVG through `svgo` with `precision: 3` (not 1 — causes wobble).
3. **Raster hygiene:** Source ≥ 1.5× delivery resolution; never upscale >110%. Convert to WebP/PNG for web, ProRes422 for edit.
4. **Audio hygiene:** Strip silence head/tail, normalize to -14 LUFS (web) / -24 LUFS (broadcast), lock sample rate 48kHz.
5. **File naming:** `01_intro_16x9_30fps_v03_remotion` — include sequence, aspect, fps, version. Version bump on every structural change.

---

## 9) Sound & Music Sync — Motion Without Audio Is Half-Slop

- **BPM lock:** If music BPM known (e.g. 120 BPM → beat = 0.5s = 15 frames @30fps), align major hits to beat ±2 frames. Pre-mark beats in timeline before animating.
- **SFX layering:** Whoosh (movement) + hit (settle) + bed (ambient). Whoosh peaks at max velocity, hit at settle frame +1.
- **VO pacing:** Never animate type faster than VO. If VO says the words, kinetic type is redundant — choose one.
- **Audio latency guard:** After render, open in VLC/QuickTime and clap-test: visual hit must be within ±40ms (±1 frame @24fps) of audio hit.

---

## 10) Rendering, Export & Delivery Matrix

### 10.1 Codec & Container
| Delivery | Codec | Container | Bitrate | Color | Notes |
|---|---|---|---|---|---|
| Web hero / social | H.264 High 4:2:0 | MP4 | CRF 18–20, max 12Mbps (1080p) | sRGB / Rec.709 | `+faststart` (moov atom front) |
| Archive / edit master | ProRes 422 (HQ) or 4444 if alpha | MOV | ~147Mbps @1080p30 | Rec.709 | Preserve 16bpc |
| Lottie/web | JSON (Bodymovin) | .json | — | sRGB | Gzip; < 250KB ideal |
| Transparent web | VP9+alpha or ProRes4444 → WebM | WebM/MOV | CRF 20 | sRGB | Test Safari fallback (H.264 matte) |
| 4K cinematic | H.264/H.265 | MP4/MOV | CRF 16–18, max 40Mbps | Rec.709 | Shutter 180°, add grain |

### 10.2 FFmpeg Production Commands
```bash
# H.264 web master — correct
ffmpeg -i in.mov -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p -movflags +faststart -c:a aac -b:a 192k out.mp4
# Verify
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,avg_frame_rate,pix_fmt -of default=noprint_wrappers=1 out.mp4
# GIF preview (dithered, not banded)
ffmpeg -i out.mp4 -vf "fps=15,scale=720:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer" preview.gif
```
- **Never:** Variable frame rate (VFR), `yuv444p` for web (breaks many players), or `-crf 28+` for hero content.
- **Always:** Check `r_frame_rate == avg_frame_rate` (constant FPS), no dropped frames in `ffmpeg` log.

### 10.3 Frame Rate & Resolution Locks
- One FPS per deliverable. If 9:16 and 16:9 needed, render twice from same comp — do not stretch or crop blindly.
- For vertical cuts, recompose (re-stage hierarchy), not center-crop landscape. Safe-area guides must be re-applied.

---

## 11) Performance & Smoothness Guarantees

- **60fps target for web, 30/24 for video:** Test on mid-tier device (Moto G / iPhone SE). If frame time > 16ms at 60fps, reduce: concurrent animated layers ≤ 6, shadows ≤ 2, blur ≤ 1 active.
- **Layer count:** Keep active composited layers < 12; merge static BG into single bitmap after design lock.
- **Debounce scroll-driven motion:** Throttle to `requestAnimationFrame`, never `scroll` event direct.
- **Memory:** For Remotion/Lottie, preload images and cap total decoded image memory < 150MB; dispose off-screen video elements.
- **Jank detection:** Record Chrome DevTools Performance trace for 10s; no long tasks > 50ms, no layout thrashing (purple bars).

---

## 12) Anti-Slop QA Gate — Must Pass Before Delivery

Run this checklist on the **rendered output**, not the preview. Hold each check; fix failures before shipping.

### 12.1 Motion & Timing (watch at 1×, 0.5×, 0.25×)
- [ ] No linear easing on organic motion (scrub graph — must curve)
- [ ] Entrance ≠ exit easing (they differ per §3.2)
- [ ] No pop on first/last frame (first frame fully hidden or intentionally visible; last frame holds ≥12 frames)
- [ ] No double-bounce or unintended overshoot
- [ ] Stagger is consistent (30–60ms) and not random
- [ ] Loop point is seamless (compare frame 0 and frame N−1 side-by-side at 400% zoom)
- [ ] No competing focal motions in same beat
- [ ] All secondary motion lags primary by 2–6 frames

### 12.2 Spatial & Visual
- [ ] No subpixel jitter on held text (pause and step frame-by-frame — glyphs do not shimmer 1px)
- [ ] No anchor wobble (rotate/scale pivots are intentional and consistent)
- [ ] No half-pixel strokes or collapsed 0.5px lines at final hold
- [ ] No tangent edges (moving element never kisses frame edge by < 4px unintentionally)
- [ ] No shadow direction change mid-scene
- [ ] Gradient banding checked on 100% and on cheap LCD (add grain if banded)
- [ ] Anti-aliasing clean at 200% zoom (no jagged diagonals)
- [ ] No raster upscaling beyond 110% (inspect 1:1 pixels)

### 12.3 Type & Brand
- [ ] Fonts rendered (no fallback flash) — checked on incognito / fresh render
- [ ] No distorted type (uniform scale only)
- [ ] No orphans/widows in final hold
- [ ] Read-hold duration passes `words × 250ms + 500ms` formula
- [ ] Brand colors hex-exact (`#` match via eyedropper, not "close")
- [ ] Logo clear-space and minimum size respected, no rotation on logo unless brand allows

### 12.4 Technical & Delivery
- [ ] Constant frame rate verified (`ffprobe` shows CFR)
- [ ] No dropped frames in encode log, no VFR warning
- [ ] Audio sync within ±40ms on clap test
- [ ] File size under platform limit (e.g. <30MB for email, <100MB for social where applicable)
- [ ] Alpha correct (if transparent: checkerboard test — no halos)
- [ ] Plays on target: Chrome, Safari, iOS, Android (web) / VLC+QuickTime (video) — at least 2 players
- [ ] Thumbnail / poster frame chosen (not black first frame)
- [ ] Filenames include `aspect_fps_version_codec` and match delivery spec sheet

> **Failure policy:** Any single unchecked box = do not deliver. Fix and re-render. For client work, attach this checklist with the delivery note.

---

## 13) Common Sloppy Patterns — Diagnose & Fix

| Sloppy Pattern | Why It Looks Cheap | Fix (Exact) |
|---|---|---|
| Everything fades+slides from same distance at same speed | No hierarchy, wall of motion | Vary distance (8px / 24px / 48px) and duration by hierarchy; stagger 40ms |
| Text slams with `easeOutBounce` | Bounce on serious content = unprofessional | Replace with `expo.out` `[0.16,1,0.30,1]`; reserve bounce for playful badges only |
| Logo scales from 0 → 100% on center | Pops, no weight | Start at 96% + 8% overshoot with `spring(stiffness:220,damping:20)` then settle to 100% + subtle opacity 0→1 over 18 frames with anticipation nudge −4px |
| Gradient BG bands badly | 8-bit without dither + large flat gradient | Add 2% grain, export 16bpc, dither on encode, never use > 40% gradient span without texture |
| Elements "swim" during hold | Subpixel positioning or continuous tiny animation | Snap to whole pixels on settle, stop rAF ticks, add 12-frame dead hold |
| Stutter on scroll/30fps vs 60fps mismatch | Mixing FPS or animating layout properties | Lock FPS, animate only `transform/opacity`, use `will-change` toggling |
| White flash between scenes | BG not extended, cut on un-filled frame | Extend BG 6 frames beyond transition, overlap 8 frames with opacity cross-fade + 1px scale |
| Audio hits late | Motion authored without beat marks | Mark BPM beats first (120 BPM = 15f @30fps), snap hits to ±2f, verify with clap test |

---

## 14) Harness Agent Workflow — Step-by-Step

When the user invokes this skill, follow this exact sequence:

**Phase 1 — Lock the Brief (5 min)**
1. Call `ask_user` if any of duration/aspect/FPS/style/copy is missing. Present 2–3 style directions (e.g. "A) Minimal kinetic type, B) Cinematic parallax, C) Playful spring") and let user choose.
2. Inspect assets via `view_file` / `list_dir`. Reject low-res logos (<800px) — request vector.
3. Produce a beat sheet table in your reply and get explicit user confirmation before animating.

**Phase 2 — Build**
4. Scaffold project: Remotion (`npx create-video` + `Root.tsx`) or GSAP (`index.html` + `style.css` + `app.js`) or Manim (`scene.py`). Keep timeline as code, not opaque binary.
5. Implement one beat at a time, previewing each. Use `execute_python` / `run_command` to render low-res proxy (`scale 0.5, fps 30, crf 28`) for quick iteration. Never show a broken proxy as final.
6. Apply timing bible (§3) and easing (§3.2) literally — do not invent curves.
7. Add audio markers early; do not bolt audio at the end.

**Phase 3 — Polish & QA**
8. Render full-res master, then run the entire §12 checklist yourself (describe results in output). Use `ffprobe` and frame-step inspection.
9. Fix failures iteratively. Re-render.
10. Export all requested aspect ratios by **recomposing** (not cropping). Show side-by-side stills of final holds for each aspect.

**Phase 4 — Deliver**
11. Deliver with: (a) master file(s), (b) poster frame, (c) 5s preview GIF, (d) source project (Remotion/Manim/GSAP source), (e) QA checklist with ticks, (f) font/license notes if type used.
12. Offer to iterate: "Want timing 10% faster, or alternate ending hold?"

---

## 15) Tool-Specific Quick References

### Viewport & Safe Guides (Inject into any comp)
```css
/* Web overlay — remove before final export */
.guide { position:absolute; inset:5%; border:1px dashed rgba(255,255,255,0.35); pointer-events:none; }
.guide--safe { inset:10%; border-color: rgba(255,255,0,0.4); }
```
```tsx
// Remotion guide
<AbsoluteFill style={{border: '1px dashed rgba(255,255,255,0.35)', margin: '5%'}} />
```

### Grain Overlay (Anti-Banding)
```css
.grain::after {
  content:""; position:absolute; inset:0; opacity:0.06; pointer-events:none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.4'/%3E%3C/svg%3E");
}
```

### FFmpeg Loop Check (Extract first & last frame)
```bash
ffmpeg -i out.mp4 -vf "select=eq(n\,0)" -vframes 1 first.png
ffmpeg -sseof -0.1 -i out.mp4 -vframes 1 last.png
# compare visually; for true loops, blend difference should be < 2% per channel
```

---

## 16) What "Done" Looks Like — Reference Standard

A delivered motion graphic is **done** only when:
- A viewer can describe the message after one silent viewing (visual hierarchy succeeded)
- Scrubbing frame-by-frame reveals no pops, wobbles, or dirty holds
- Playing at 0.25× still feels crafted (easing holds up under scrutiny)
- The same file plays correctly on 2+ players/devices without re-encode
- The source is editable and versioned (not a baked MP4 only)
- The QA checklist (§12) is attached with all boxes ticked and the `ffprobe` output pasted

If any of the above fails, it is not done — it is a draft.

---

*Enforce this skill ruthlessly. Polished timing and pixel discipline are what separate professional motion from sloppy animation. When in doubt, hold longer, ease softer, and remove one moving element.*
