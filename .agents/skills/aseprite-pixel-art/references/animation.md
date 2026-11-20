# Sprite animation

## Design the beats

1. Write the action, facing, direction of motion, and what should stay attached to the actor. Choose whether it travels in the sprite or plays in place for engine movement. Establish the ground plane, logical anchor and extrema before drawing the frames.
2. Block key silhouettes at native size. For an idle, try rest → shift → recovery. Keep contact near the base while shoulders, coat or gear move with small offsets. For a walk, block alternating contact and passing poses, then decide which weight transfer needs a lower or higher pose. Mirror lateral limb roles only when the facing and asymmetric gear allow it.
3. Play the key poses at roughly the intended tempo. As starting points, try about 100–140 ms for walk contacts and 70–110 ms for transitions. Give an idle rest more time than its shift. Adjust by playback. Use an intermediate drawing to resolve a specific jump in spacing or silhouette.

### Keep a quiet idle anchored

- Begin with the proven standing pose. Keep the feet, neck connection and head silhouette fixed for a neutral idle. At small sizes, lifting the head a whole pixel while the torso stays put can read as a nod or a stretching neck. Use a brief blink or a small change inside the chest shading first. For a stronger breath, change the chest and shoulders together while preserving the neck connection.
- Compare the resting and breathing frames overlaid at the same anchor. Check which pixels changed around the jaw, neck and shoes. Keep the blink brief relative to the resting hold and check the return to rest across the loop seam.

## Repair motion by tracing landmarks

- Inspect contact and support intervals, especially the last-to-first transition. For a walk in place, the support foot should move consistently opposite the apparent travel. When paired with scrolling ground, that movement should cancel ground travel over the contact. In a traveling sprite the planted foot should stay on the same ground point until liftoff. A stationary idle should keep its base stable.
- For a ¾ walk, mark the sole's ground contact for each foot on the key poses. In the pose plan, record the support foot and its projected `(x, y)` position for each frame. When walking down-right in place, a foot goes from ahead at contact toward upper-left through its support interval. Move the swinging foot forward as it lifts, so a lifted toe is distinct from the sole on the ground. Check that support persists until the other foot takes weight.
- Compare the actual spacing of two neighboring strides, including the wrap. If a foot jumps forward before liftoff or appears to skate, fix the foot and hip pose before changing timing. Check that the pose at the weight transfer has a visibly planted sole at native size.
- Use a stable ground reference when checking: a fixed shadow if the style calls for one, or an anchor mark on a temporary preview. Compare the last two and first two walk poses side by side. Identify which sole is bearing weight in each. If the last pose has both feet tucked and the first lands abruptly, redraw the last pose with its support foot on the ground and the other foot approaching contact.
- For a 32×32 low-impact walk, zoom into just the boots in the approach and following contact poses. Trace the approaching sole into the contact frame on the same grid. Aim for a final vertical step of about one native pixel when the foot is already extended. If it drops farther, either lower the approach toe or add a pose that bridges the gap. Keep the other sole on its support path until transfer. Apply the same check at both half-step boundaries.
- Compare head, hips, hands, feet and any rigid gear relative to the anchor. Use onion skin or consecutive frame renders to find accidental one-frame jumps. Preserve intentional weight shifts. Keep shadows, outfit markings and key color clusters attached to the same forms.
- Check the seam at the actual tempo with at least two consecutive cycles. A duplicated endpoint can insert an unintended pause. Use a longer hold on the intended pose when a pause is desired.
- Put idle and walk in independently playable tags. Inspect representative poses and the seam together. Lint samples one selected frame at a time, so request a specific frame when investigating a flicker.

### Start a short walk with two matching half-steps

For a small sprite, try contact → load → pass → approach contact, then repeat those four beats with the limbs exchanged. At contact the new sole meets the ground in front. At load the weight comes over that sole as the other foot lifts. At pass the bent swing leg crosses the support leg. At approach contact the swing foot extends low and the old support remains down until transfer. Start with modest reach and body bob, then exaggerate only if the action reads poorly. Keep asymmetric clothing and equipment on the same side across both halves.

### Check a four-direction set

- Lay out all standing poses on the same ground reference at 1×. Check what actually survives overlap. A wide hat can hide the front eyes, and shoulder straps or patch pockets can distinguish the back. When mirroring a side pose, reapply highlights if the project has a fixed world light.
- For north and south, author the foreshortened foot path directly on the pixel grid. Rounding a sideways stride can make the boot jump or repeat a position. Use a separate near/far lane for lateral poses. Keep arm swing opposite the legs and check that the far arm reads at its furthest point.
- Compare the last two and first two poses of each walk beside the equivalent middle transfer. When the approach toe and contact toe land close together, check their combined hold. Preserve a visible weight shift between them or shorten the approach so the loop keeps its pace. Check the pose with one leg layer or a temporary landmarks overlay if the torso obscures a sole.

[Courier source](../assets/courier.aseprite) contains both tags. Compare the [idle loop](../assets/courier-idle.gif) and [walk loop](../assets/courier-walk.gif) for differences in contact, timing and secondary motion. The [contact sheet](../assets/courier-contact-sheet.png) shows every exported pose with its GIF frame duration.
