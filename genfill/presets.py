"""Ready-made instructions for Generate, like Photoshop's Neural Filters. Picking one runs it straight
away. With a selection they work on that area; without one, on the whole image."""
from __future__ import annotations

KEEP = " Keep everything else exactly the same: same people, objects, composition and framing."

PRESETS: list[tuple[str, str]] = [
    ("Harmonize (select the pasted object)",
     "Harmonize the object in this area with the scene: match its lighting direction, colour temperature, "
     "contrast, shadows and grain to the rest of the photo. Keep its shape and design the same."),
    ("Colorize black & white",
     "Colorize this black and white photo with natural, realistic colours: true skin tones, plausible clothing "
     "and surroundings, subtle period-correct colour." + KEEP),
    ("Restore old photo",
     "Restore this old photo: remove scratches, dust, creases, stains and noise, fix fading and colour casts, "
     "recover sharp detail." + KEEP),
    ("Sky: golden sunset",
     "Replace the sky with a warm golden-hour sunset sky with soft clouds, and adjust the scene's light to "
     "match it." + KEEP),
    ("Sky: clear blue",
     "Replace the sky with a clear blue sky with a few light clouds, and adjust the scene's light to match "
     "it." + KEEP),
    ("Sky: dramatic storm",
     "Replace the sky with dark dramatic storm clouds, and adjust the scene's light to match it." + KEEP),
    ("Remove reflections: windows",
     "The photo looks through a window into a room. Remove only the reflection on the glass: the faint, "
     "see-through mirror image of the outdoor scene in front of the window (the street, pavement, cars, poles, "
     "buildings across the road, sky). Keep the room behind the glass and everything in it, its people, furniture, "
     "bottles, signs and warm lights, and make it clearer. Keep the window frame, the colours and the light the "
     "same."),
    ("Remove reflections: glasses",
     "Remove the glare and reflections on the lenses of the glasses so the eyes behind them are clearly visible, "
     "as if photographed with a polarizing filter. Keep the frames, the lenses, the eye colour, the skin tone and "
     "the face exactly the same." + KEEP),
    ("Background blur (portrait lens)",
     "Make the background softly out of focus like a portrait lens at f/1.4 with creamy bokeh; keep the main "
     "subject perfectly sharp." + KEEP),
    ("Relight: golden hour",
     "Relight the scene with warm, low golden-hour sunlight and long soft shadows." + KEEP),
    ("Relight: soft studio",
     "Relight the scene with soft, even studio lighting from a large softbox, clean neutral colours." + KEEP),
    ("Relight: night",
     "Turn the scene into night: dark blue ambient light, artificial lights and lamps glowing warmly." + KEEP),
    ("Portrait: natural smile",
     "Make the person smile naturally and warmly." + KEEP),
    ("Portrait: look at the camera",
     "Make the person look straight into the camera." + KEEP),
    ("Portrait: retouch skin",
     "Retouch the skin: remove blemishes and redness, even out the tone, keep natural skin texture and "
     "pores. Do not change the face's shape or features." + KEEP),
    ("Portrait: younger",
     "Make the person look about ten years younger, naturally." + KEEP),
    ("Portrait: older",
     "Make the person look about twenty years older, naturally." + KEEP),
]

# Presets that should change only some things, not the picture's colours: whole-image results are colour-matched
# back to the original afterwards (Klein drifts warmer and darker when it redraws everything).
KEEP_COLOURS = {"Remove reflections: windows", "Remove reflections: glasses"}
# Presets that run without the user's LoRAs: a LoRA trained on something else holds the picture as it was,
# so reflections stay (tested: with one the reflected car and street remained, without they went).
NO_LORAS = {"Remove reflections: windows", "Remove reflections: glasses"}

PLACEHOLDER = "Presets…"


def prompt_for(name: str) -> str | None:
    return dict(PRESETS).get(name)
