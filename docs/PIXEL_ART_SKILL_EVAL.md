# Pixel art skill evaluation

Each pair uses the same brief, input, `gpt-6.2-sol` at medium reasoning effort, real Aseprite executable and isolated MCP workspace. The first pass supplies sandboxed Python to the MCP server. Each arm receives the rendered output, tool status and inspection metadata before one opportunity to revise. The final comparison gives each arm a second review focused on the seam. The skill arm also receives the skill instructions and art references as text. Both arms draw from the brief or the supplied damaged sprite.

The standalone Codex CLI policy blocked shell reads and direct MCP tool calls in both arms. In the completed trials, a local runner submitted the model's scripts through `execute_code` and retrieved renders through `inspect_sprite`. This tested the effect of the skill instructions on the same tools. Automatic skill invocation and tool access from a standalone Codex session remain unverified.

## Briefs and blind scoring

| Brief | Primary criterion |
| --- | --- |
| Draw a 32×32 teal lantern keeper in top-down ¾, facing down-right. Use upper-left light, a limited palette, and transparency for a middle-value warm gray ground. Deliver editable source and a native-size PNG. | Color roles and value grouping |
| Draw a 32×32 teal field scout in top-down ¾, facing down-right. Add a subtle idle and a walk in place with alternating weight, ground contact, a smooth seam and a common anchor. Deliver editable source and GIFs for both tags. | Weight-bearing foot contact and the walk seam |
| Diagnose the damaged courier, then repair its cap edge, lighting and accidental pixels in a copy. Preserve the face, outfit, clasp and framing. Deliver editable source and a native-size PNG. | Precision of cap lighting and stray-pixel fixes |

A fresh grading session scored anonymously labeled previews on a 1–5 scale, with 5 strongest. Still sprites were shown enlarged and at native size. Motion scores use ordered GIF frame renders and exported timings. The grader did not play the GIFs in real time. The folders holding the source files and model responses were outside the checkout.

## First trial

| Primary criterion | Control | Skill | Observation |
| --- | ---: | ---: | --- |
| Static color and values | 5 | 4 | The skill version's cheek and lantern accents competed for attention. |
| Walk contact and seam | 4 | 3 | The skill version had a better-paced idle, but the long reach in walk frames 5–6 obscured which foot was supporting weight. |
| Repair lighting and stray pixels | 4 | 5 | Both versions removed the detached pixel and preserved the face, coat and clasp. The skill version grouped the cap highlights more coherently. |

After this trial, the drawing reference gained a native-size grayscale and focal-value check. The animation reference gained an explicit check of the projected support-foot position and the transfer of weight across adjacent poses and the seam.

## Second trial

| Primary criterion | Control | Skill | Observation |
| --- | ---: | ---: | --- |
| Static color and values | 4 | 5 | The skill version grouped the cool coat and kept its largest, brightest warm cluster in the lantern. |
| Walk contact and seam | 4 | 3 | The skill version's feet tucked higher in the last pose and dropped into the first. The control's fixed shadow provided a clearer ground reference. |

The second trial repeated the static and motion briefs. The animation reference then gained an explicit four-beat half-step and a ground-reference check over the final and initial pairs of frames.

## Third trial

The motion control scored **4/5** and the skill scored **3/5** for foot contact and the seam. Both scored **5/5** for pose coherence and idle timing. The skill version still dropped its leading toe noticeably at the seam. The animation reference gained a close-up check that traces the approaching sole into contact at both half-step boundaries.

In a second review of the same outputs, both arms received their own seam close-up and could make one further edit. The skill corrected the toe drop, but frames 8 and 1 held nearly the same forward reach for a combined 200 ms. The blind score remained **4/5** for the control and **3/5** for the skill. These runs show gains on static color grouping and repair precision. The walk-contact and seam acceptance criterion remains unmet.

## Tool verification

- Aseprite opened the original 32×32 courier source with two named layers, 12 frames, and separate idle and walk tags. MCP re-exports matched the bundled native PNG and both GIFs, including the individual GIF frame durations.
- Read-only inspection located the deliberately detached pixel at `(28, 5)` and left the input byte-for-byte identical. Both repair arms also preserved the original.
