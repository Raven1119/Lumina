---
name: lumina-interface-language
description: Apply Lumina's quiet bioindustrial visual language with mineral, depth and neutral stone palettes, and the frozen Research Interface Language 1.0.0 motion baseline. Use for Lumina frontend appearance, interaction and design-system implementation.
---
# Lumina Interface Language

## Read first

Read `references/DESIGN.md`, inspect `assets/reference.html` and its screenshots.
When motion is involved, read `references/MOTION.md` and the required entry in
`assets/motion-map.json`; inspect its code in `assets/motion-reference.html`.

## Authority

- Visual: this package's Lumina reference, namespaced tokens, screenshots.
- Motion: the embedded RIL 1.0.0 / V9 implementation, locked by source-block hashes.
- Implementation: project business behavior and explicit task acceptance.
- Demo layouts, texts and central geometry are examples, not product requirements.

## Invariants

Preserve the matte, low-chroma, layered visual language: precise structure,
softened material, sparse living accents, readable text, quiet resting state.
Use only `mineral` (矿灰), `depth` (幽青), `stone` (岩灰).
Stone tokens are neutral gray, including accent; no purple theme remains.
Do not import CyberScientist's dashboard skin, heavy instrumentation, or franchise art.
Do not treat every panel as a creature. Avoid forcing the specimen into an avatar.

Motion is the original approved family of 15, not the old Lumina plate-keyframe demo.
Keep the shader formulas, algorithms, durations, easing, tile phases and disposal.
Do not recreate motion by approximate CSS, random wobble, breathing, or new springs.
Select only effects needed by a real interaction; no requirement to use all 15.
Normal text, editor position and existing messages stay still.
No simulated cognition or success inferred from decoration.

## Minimal integration

1. Inspect the existing frontend and select the smallest real functional slice.
2. Import `assets/tokens.css` and `assets/primitives.css`; place `.lui` and
   `data-lui-theme="mineral"` on the app root. Reuse the tokens in existing components.
3. For required motion, locate the exact implementation in the reference application.
   Keep associated styles, host lifecycle and notices; see IMPLEMENTATION.md.
   Do not package restricted upstream components as a separate distributable library.

Use system font stacks; no font binary is bundled or needed.
Keep `.lui` isolated from pages using the research language. Do not overwrite another
skill's managed instruction block. If two skills cover the same frontend, choose
Lumina explicitly for this task rather than merging visual tokens.

## Validate

Run `python verify.py`, applicable tests, and `references/CHECKLIST.md`.
Test three themes, small screens, keyboard, reduced motion, immediate completion
when motion is disabled, and every effect actually used. Dispose scenes on unmount.
Report only observed results; distinguish unavailable graphics capability from pass.
Do not modify the shared baseline to patch a single project's task.
