---
name: aseprite-pixel-art
description: Draw, revise, or critique pixel art in Aseprite with aseprite-codemode MCP. Use for character sprites, creatures, items, icons, props, tiles and environments, or for planning and checking sprite animation, palettes and pixel cleanup.
---

# Pixel Art in Aseprite

1. Choose the requested mode. **Draw** makes a new asset. **Revise** inspects the original, states the intended change, and works on a copy. **Critique** uses read-only inspection, then reports the highest-impact issues, their locations or frames, and actionable fixes.
2. Inspect the project art and the asset's intended use. Extract the viewpoint, native size, display background, palette roles, light direction, outline treatment and motion conventions from relevant examples. When there is no existing direction, choose one, state the assumptions briefly and make one representative sample before expanding a set. Ask when a missing decision materially changes the asset and cannot safely be inferred.
3. Read [Shape, color and style](references/drawing.md) for new art or visual revisions, [Sprite animation](references/animation.md) when motion is involved, and [Review and handoff](references/review.md) when inspecting or preparing a deliverable. The linked examples are original practice assets. Use the project's own art direction for production.
   For a pixel-art screenshot, follow [Recover a sprite grid from a screenshot](references/drawing.md#recover-a-sprite-grid-from-a-screenshot) before animating or revising the pose.
4. For a new character, review one representative standing pose at native size before making other facings or animation frames. Use the [character design checks](references/drawing.md#design-a-character-before-animating) to decide whether the silhouette, face and identity cues survive at 1×. If a major cue fails, revise the pose and look at it again before expanding the set.
5. Review and revise up to two times, focusing on the most visible remaining issue per pass. Ask if the fix would change an agreed direction or if a material issue remains after those passes. Report visual quality and technical checks separately.

## Aseprite workflow

- Discover exact SDK calls with MCP `search_tools`. Use `get_guide` for tool usage when needed, including `pixel_art`, `animation`, `idle` and `batch_lua`.
- Use `execute_code` with `ase.batch` and named layers for coordinated edits. For hand-authored indexed rows, check the color map and bounds, then inspect the composite using `auto_view` or `ase.show`. Use the actual requested working paths. For revisions, copy the original .aseprite file byte-for-byte to a new authorized path inside the MCP workspace before writing. Use a workspace file copy so layers, tags and metadata travel with it.
- Use `inspect_sprite` with scale 1 for native pixels and an integer scale for close review. Frame selections and SDK tools can differ in indexing. Verify the relevant signature before editing. Export previews through the discovered SDK export calls in `execute_code`.
- Deliver editable `.aseprite` with meaningful layers and animation tags where applicable, plus PNG for static art or a GIF for animation. Note any visual check you could only perform on static frames.
