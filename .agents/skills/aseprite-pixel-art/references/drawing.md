# Shape, color and style

## Block in the subject

- Sketch the envelope and the largest masses with a few opaque clusters. Read the silhouette as a single flat color. Use a gap, an offset, or a distinct shape to separate details that tell the viewer what the object is.
- For a 32×32 top-down ¾ character, agree on how much of the top and front planes are visible, where the feet contact the ground, and which direction the character faces. Keep head, body and gear proportions recognizable at native size. Reserve a margin for the farthest pose if the sprite will animate.
- For an item or icon, pick one identifying contour and one interior contrast. Try it on the actual HUD background at native size. For props and environment art, compare against a character for scale and preserve contrast for the assets the player needs to notice.

## Design a character before animating

1. Pick the facing that shows the defining parts of the design together. Name two or three visible cues that should identify the character. For a farmer, test whether the silhouette and clothing carry the role together. A straw hat, readable overall straps and a bib pocket give three places to look.
2. Block the head, torso, arms and legs as distinct masses. If the first silhouette looks squat or rigid without intent, compare two small alternatives that change proportions or pose. Try a narrower hat and longer body, or a looser arm angle and a clearer stance. Choose the stronger silhouette before adding texture or extra colors.
3. Export a clean preview and look at it unscaled on the destination background. Check the silhouette as a solid shape, then inspect the rendered pose. Find the actual pixels for each defining cue. Check that the hat leaves room for the eyes if the face should show, that the arms separate from the torso at an intentional edge or gap, and that light and dark clusters make the clothing parts readable. If only one accessory carries the identity, redesign the supporting shapes.
4. For a directional set, validate a profile pose before deriving the other sidelong view. A profile needs its own face projection, shoulder overlap, near/far limb placement and recognizable clothing cues. Compare the profile and front before animating either pose. Also check which identity cues remain visible from behind.

## Recover a sprite grid from a screenshot

1. Zoom with nearest-neighbor and look for repeated color edges along each axis. Estimate cell spacing and grid offset from several clear edges. Check a few candidate alignments across the hair, eyes and shoes. A noninteger screenshot scale is common. Treat the occupied grid size and the original canvas size as separate estimates.
2. Read each cell near its center. Where compression or blur affects the sample, use a small cluster of central samples and group the colors by material. Trace the silhouette on the grid to separate dark outlines from dark background. Keep the original pose landmarks and overlaps, especially the hands, eyes, clothing edges and planted feet.
3. Compare the reconstructed standing pose beside the screenshot at matching apparent size, then inspect the sprite at 1×. Correct shape, spacing and color groups before animating. Render each recovered cell as one flat color. Preserve hard edges and transparent background in the working sprite.

## Paint by role

- Use the existing palette when one is supplied. For a new palette choose a dark anchor, local colors, a shared shadow or light where materials permit it, and an accent. Group values before tuning hue and saturation. Reduce near-duplicate colors if they serve the same role. A material can use two values if its volume reads.
- Assign the clearest light/dark contrast to the focal feature. For a lantern carrier, reserve the brightest small cluster for the lantern core and keep nearby skin or trim in a quieter value range. Check a grayscale preview at 1×. If the hood, face and prop compete, lower the value of the largest competing patch before adding shades.
- Mark the light direction and shade planes and cast-shadow regions from it. Keep hits of light concentrated on the lit planes or useful material details. If an evenly dark edge surrounds a bright center, flatten the center and place the shadow on a consistent side.
- Use outlines where they help on the target background. Try a softer edge or a local shadow color when a uniform dark outline obscures the form. Use hand-placed anti-aliasing sparingly where an intermediate palette color clarifies a curve at native size. Recheck the edge on the gameplay background, especially for transparency. Dither only where the texture or transition contributes to the intended look at scale.

## Clean up the pixel structure

- Look for accidental changes in stair-step runs along a smooth contour. Adjust the run lengths where the contour should be steady. Preserve irregular steps that communicate perspective, a feature or intentional texture.
- Join noisy singletons into planes when they describe one surface. Leave isolated eyes, sparkles and accents when they remain legible and intentional. At native size, check that the most prominent contrast belongs to the intended focal point.
- For tiles, repeat a tile 3×3, check both axes and the four-way corner, and check a second arrangement with any intended neighbor variants. Move a hard edge or conspicuous mark if repetition creates a seam or unwanted grid.

[Annotated before-and-after examples](../assets/art-examples.png) show a rough contour and noisy centered lighting corrected on a field courier. The bottom rows show a small item at native size and a repeated ground tile.
