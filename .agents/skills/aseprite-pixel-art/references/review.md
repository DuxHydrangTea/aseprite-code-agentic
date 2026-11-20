# Review and handoff

## Review in impact order

1. At native size on the destination background, describe what you can see before checking it against the brief. Can you identify the subject, its facing and its action? For a character, name the visible role cues, check that the proportions suit the intent and point out where overlaps erase a feature or make the pose stiff. For asset families, compare size, palette, light direction and detail density against the closest peer.
2. Zoom with nearest-neighbor scaling. Check contour cadence, value grouping, unintentional alpha or stray pixels, and details that disappear at native scale. Inspect each lint hit in context. A purposeful eye, speck or spark can be a singleton.
3. For animation, read the pose strip in order, including the wrap. Play each tag at its frame timings. Compare anchor, contact and consistency of volume and details. Check for flicker by inspecting the specific frames where a shape changes. When playback is unavailable, inspect adjacent frames and durations and disclose that playback was unavailable.
4. For critique, give the user a short prioritized list. For each issue, name the location or tag and frame, what is visible, and the smallest useful change. If an expected violation is actually purposeful, preserve it.

Record separate conclusions for character design, motion readability and technical delivery. If a character still fails the brief at native size, describe the visual blocker plainly even when its complete frame set and exports pass.

To practice the review mode, inspect the [damaged courier](../assets/courier-repair.aseprite) before opening the [annotated correction](../assets/art-examples.png). Both are original training assets.

## Preview and deliver

- To revise a working copy through MCP, pass new workspace-relative paths to the guarded Python built-in `open` in `execute_code`. Use binary mode to preserve all source data and exclusive creation to protect any existing working file.

  ```python
  with open("incoming.aseprite", "rb") as source:
      with open("working.aseprite", "xb") as target:
          target.write(source.read())
  ```

- Discover `view_sprite`, `export_frame`, `export_spritesheet`, `set_tag` or `export_animation` by capability. `inspect_sprite` renders a PNG with rulers and reports metadata and diagnostics. To judge the image alone at 1×, export a clean frame and view it unscaled. Composite a preview on a temporary copy if the destination background matters.
- For GIF playback, export each relevant tag via `ase.export.export_animation` and open it in a viewer that plays GIFs. Use the sprite's frame durations. For a large sprite strip, inspect adjacent frame ranges to keep drawings legible. Color stats and lint from a range describe its first frame.
- Review the exact files you will deliver after the last export. Open the source and verify its tags and dimensions. For a walk, play the exported animation at its intended speed for at least two cycles at 1×, then inspect the transfer frames enlarged. Look for feet merging into one shape, detached limbs, flickering clusters and pauses at the seam. A valid file and a correct frame count establish technical delivery. If a visible defect remains, fix it and repeat the review on the new export, or describe the unresolved issue. For revise or critique, check that the source file still matches the initial version.
