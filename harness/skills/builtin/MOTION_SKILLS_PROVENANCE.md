# Vendored Motion Skills — provenance

The 54 skill folders in this directory are vendored from the open-source
**iart-ai motion-skills** collection (<https://github.com/iart-ai/motion-skills>),
which is a meta-repo pointing at 17 independently published packs. Vendored
under the same MIT license — see `MOTION_SKILLS_LICENSE` (MIT © 2026 iart.ai).

## What changed during vendoring

Only the two Harness-specific frontmatter keys were rewritten. **Everything else
is byte-identical to upstream** (asserted by a diff check in `tests/test_skills.py`):

| Field | Upstream | Here |
|---|---|---|
| `description` | upstream trigger blurb | **unchanged (verbatim)** |
| `name` | `kebab-case`, `lower-thirds` in two packs | snake_case, collisions disambiguated |
| `triggers` | absent (Harness-only concept) | 4–6 discriminative keywords |
| instruction body | upstream | **unchanged (verbatim)** |
| `references/`, `scripts/`, `templates/`, `examples/` | upstream | **unchanged (verbatim)** |

Pack-local `README.md` files were dropped (they only linked back to the pack's
own repo table). Showcase GIFs/HTML at pack root were never copied.

## Pack → skill map

| Pack | Skills (Harness names) |
|---|---|
| motion-design-skills | `animation_principles`, `beat_sync_editing`, `color_motion`, `shot_composition`, `motion_art_direction`, `motion_background`, `after_effects`, `remotion_video`, `logo_animation` |
| web-animation-skills | `gsap_web`, `svg_animation`, `lottie_animation`, `micro_interaction`, `glassmorphism`, `page_transition_animation`, `accessible_animation`, `fps_60_animation`, `ascii_animation` |
| webgl-animation-skills | `threejs_animation`, `shader_glsl`, `particle_system` |
| kinetic-typography-skills | `kinetic_typography` |
| javascript-animation-skills | `javascript_animation`, `soundtrack` |
| generative-illustration-skills | `generative_illustration` |
| explainer-video-skills | `explainer_video`, `diagram_animation`, `isometric_animation`, `whiteboard_animation`, `wrapped_video` |
| data-animation-skills | `chart_animation`, `animated_infographic`, `presentation_video` |
| manim-skills | `manim` |
| map-animation-skills | `map_animation` |
| lower-thirds-skills | `lower_thirds_overlay` |
| tiktok-video-skills | `short_form_video`, `caption_animation`, `countdown_video`, `lower_thirds` |
| youtube-video-skills | `audiogram`, `youtube_intro_outro` |
| text-message-video-skills | `text_message_animation` |
| ecommerce-video-skills | `photo_slideshow`, `product_demo_video`, `promo_video` |
| ad-video-skills | `ad_creative_video`, `launch_video`, `testimonial_video` |
| freelance-motion-skills | `creative_brief`, `motion_pricing`, `client_revisions`, `video_delivery_specs`, `brand_motion_guidelines` |

## Two upstream name collisions

Both packs ship a skill named `lower-thirds`, so the names were disambiguated:

- **`lower_thirds`** ← tiktok-video-skills. The *design/craft* skill: enter/exit
  animation, dwell timing, safe areas, type hierarchy. Ships `references/lower-third-component.md`.
- **`lower_thirds_overlay`** ← lower-thirds-skills. The *delivery* skill: transparent
  alpha via WebM/ProRes 4444 for OBS / Premiere / Resolve. Ships `scripts/render.mjs`
  and `templates/lower-third.html`.

## `motion_verify` (not upstream)

Upstream, each of the 14 packs carried its own copy of the same three verify scripts at
the pack root (`scripts/seek-shot.sh`, `scripts/contact-sheet.sh`, `scripts/probe-mp4.sh`),
and 41 of these skills reference them as `scripts/…`. Since Harness has no pack root,
the toolkit lives in one place — **`motion_verify/`** — with the three scripts vendored
verbatim from upstream `tools/verify/`. Load that skill to resolve a `scripts/…` reference.