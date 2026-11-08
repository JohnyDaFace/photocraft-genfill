"""Styles: a description of a look, put before the prompt while the style is on (the palette button), for
Create and whole-image edits. Not for fills, which must match the photo around them. With a style on and the
prompt left empty, Generate on the whole image just applies the style.

Written the way Black Forest Labs advise for Klein: descriptive sentences heavy on light and atmosphere (not
tag lists: "8k, best quality" does next to nothing), about 25-40 words so the whole prompt stays near their
40-70, real camera and film names for photo looks, and saying what to show rather than what not to.

The built-in styles come from here, so a new version can improve them. styles.json (next to settings.json)
holds only yours: new styles, a built-in's name with your own words to change it, and names to hide."""
from __future__ import annotations

import json
from pathlib import Path

from .settings import CONFIG_DIR

STYLES_FILE = CONFIG_DIR / "styles.json"

KEEP = "Keep the same people, objects, composition and framing."  # restyling with no prompt
KEEP_PRODUCT = ("Keep the product itself exactly the same: its shape, colours, materials, label and details. "
                "The setting, angle and light may change.")
KEEP_MODEL = ("Keep the product itself exactly the same: its shape, colours, materials, label and details. "
              "Add one woman holding it; the setting and light may change.")
KEEP_FOOD = "Keep the same dish and food. The setting, angle and light may change."
KEEP_ERA = "Keep exactly the same number of people, the same faces and poses, and the composition."
KEEP_ROOM = "Keep the same room, furniture and layout."
KEEP_FIGURE = "Keep the person's likeness, pose, hair and outfit colours."
# "same composition" left Fashion editorial nothing to change (and it grew a stray hand); this frees the outfit
KEEP_FASHION = "Keep the same person, their face and pose; the outfit styling, setting and light may change."
KEEP_FACE = ("Keep the same person with an identical face, features, eye colour, skin tone, expression and "
             "hair. The light, backdrop and framing may change.")
KEEPS = {**{n: KEEP_MODEL for n in ("Model studio", "Model close-up", "In hand", "Model lifestyle",
                                    "Model outdoors")},
         "Food overhead": KEEP_FOOD, "Food dark & moody": KEEP_FOOD, "Interior bright": KEEP_ROOM,
         "Toy figure": KEEP_FIGURE, "Fashion editorial": KEEP_FASHION}

DEFAULT_STYLES: list[tuple[str, str]] = [
    ("Professional photo",
     "Professional photograph shot on a Sony A7R V with an 85mm f/1.8 prime, soft directional window light "
     "shaping the subject, clean high dynamic range, true-to-life colours, crisp fine detail."),
    ("Film photo",
     "Analog film photograph on Kodak Portra 400, warm natural daylight, soft organic grain, gentle highlight "
     "roll-off, creamy skin tones and slightly faded, nostalgic colour."),
    ("Cinematic",
     "Cinematic film still from a big-budget drama, anamorphic widescreen lens, motivated practical lighting with "
     "deep shadows, teal and amber colour grade, light haze in the air, subtle 35mm grain, shallow depth of field."),
    ("Movie poster",
     "Dramatic movie poster look, bold heroic composition, striking rim lighting, high contrast, rich saturated "
     "colours, polished high-end finish. No text, no title, no lettering anywhere in the picture."),
    ("Golden hour",
     "Golden hour photograph, low warm sun just above the horizon, long soft shadows, glowing rim light on hair "
     "and edges, warm amber and peach tones, gentle lens flare."),
    ("Low-key",
     "Moody low-key photograph, a single hard light from the side carving the subject out of deep shadow, "
     "dramatic chiaroscuro contrast, rich blacks, muted colours."),
    ("Black & white film",
     "Black and white photograph on Ilford HP5 film, deep blacks and luminous highlights, rich mid-tone contrast, "
     "fine natural grain, timeless classic look."),
    ("Instant photo",
     "Instant film photograph with a thick white border, soft dreamy focus, faded warm colours, gentle light "
     "leaks and a slight vignette, casual candid feel."),
    ("2000s digital",
     "Early 2000s compact digital camera snapshot, harsh direct on-camera flash, slightly blown highlights, cool "
     "colour cast, mild noise and softness, candid unposed moment."),
    ("Fashion editorial",
     "High-fashion magazine editorial photograph, bold styling and a confident pose, crisp flattering "
     "beauty light with a soft glow on the skin, refined colour grade, clean luxurious finish."),
    ("Interior bright",
     "Bright real-estate interior photograph, tidy and decluttered, wide-angle lens with perfectly "
     "straight vertical lines, airy soft daylight filling the room, balanced exposure through the windows, "
     "clean inviting look."),
    # portraits: close-up head and shoulders, each a classic lighting setup; restyling keeps the face (KEEP_FACE)
    ("Editorial",
     "Close-up editorial portrait, head and shoulders, large soft key light from one side and a gentle rim light, "
     "natural skin texture, catchlights in the eyes, creamy out-of-focus background, refined colour."),
    ("Corporate headshot",
     "Professional corporate headshot, head and shoulders, smart neat clothing, soft even front light, a clean "
     "light grey studio backdrop, friendly confident expression, crisp sharp eyes."),
    ("Actor headshot",
     "Natural actor headshot, close-up head and shoulders, soft window daylight, relaxed genuine expression, "
     "honest skin texture, a softly blurred neutral background, eyes in perfect focus."),
    ("Rembrandt light",
     "Close-up portrait photograph in Rembrandt lighting, one soft light high to the side leaving a small "
     "triangle of light on the shadowed cheek, deep warm shadows, a plain dark background, rich natural skin tones."),
    ("Beauty light",
     "Close-up beauty portrait in butterfly lighting, soft light from straight above the face, a small shadow "
     "under the nose, glowing even skin, sculpted cheekbones, bright clean background."),
    ("Split light",
     "Dramatic close-up portrait in split lighting, one hard light from the side lighting exactly half the face "
     "while the other half falls into shadow, black background, intense mood."),
    ("Rim light",
     "Close-up portrait photograph against a dark background, soft backlight giving the hair and shoulders a "
     "subtle natural glow at the edges, gentle fill light on the face, cinematic and mysterious."),
    ("Window light",
     "Intimate close-up portrait lit by soft daylight from a nearby window, gentle falloff into shadow, natural "
     "skin and catchlights, quiet calm mood, muted warm tones."),
    ("Colour gels",
     "Close-up portrait lit with coloured gel lights, vivid magenta from one side and teal from the other mixing "
     "on the skin, dark background, bold modern nightlife mood."),
    ("B&W portrait",
     "Classic black and white close-up portrait photograph, rich contrast, deep blacks and soft bright "
     "highlights, flattering light on the face, sharp eyes, timeless elegant look."),
    ("Colour backdrop",
     "Bright studio close-up portrait in front of a bold solid colour backdrop, clean even light, vivid "
     "saturated colour, playful modern fashion feel, crisp detail."),
    # products: each brings its own angle and set; restyling keeps the product itself (KEEP_PRODUCT)
    ("Studio white",
     "E-commerce product photograph, three-quarter view, pure white seamless background, large soft even "
     "lighting from both sides, a faint natural shadow under the product, true colours, tack-sharp detail."),
    ("Hero shot",
     "Heroic advertising product photograph from a low angle looking up, dark graphite backdrop, dramatic rim "
     "light tracing the edges, a soft glow behind the product, glossy premium finish."),
    ("Flat lay",
     "Top-down flat lay product photograph shot straight from above, the product centred on a clean matte "
     "surface with a few neatly arranged complementary props, soft diffused daylight, gentle shadows."),
    ("Lifestyle",
     "Lifestyle product photograph, the product in use in a cosy real home setting, warm natural window light, "
     "shallow depth of field with a softly blurred background, authentic and inviting."),
    ("Luxury",
     "Luxury product photograph on polished black marble, deep velvet shadows, a warm spotlight with golden "
     "highlights and accents, rich reflections, elegant and expensive."),
    ("Podium",
     "Minimal product photograph, the product standing on a round pastel pedestal, soft pastel backdrop, gentle "
     "studio light with soft rounded shadows, clean modern composition."),
    ("Splash",
     "High-speed product photograph, crystal-clear water splashing around the product frozen in mid-air, crisp "
     "strobe lighting, sparkling droplets, fresh and dynamic, clean gradient background."),
    ("Nature",
     "Natural product photograph outdoors, the product resting on mossy stone and weathered wood, dappled "
     "sunlight through leaves, earthy green tones, soft depth of field."),
    # products held by a model: the product stays exactly the same (KEEP_MODEL)
    ("Model studio",
     "Commercial product photograph of a young woman holding the product up beside her face with one hand, smiling "
     "at the camera, the product sharp and facing the camera, soft even studio light, plain light backdrop."),
    ("Model close-up",
     "Beauty advertising close-up of a young woman's face and shoulders, the product held gently against her cheek, "
     "glowing skin, soft flattering light, clean pastel backdrop, the product label sharp and readable."),
    ("In hand",
     "Close-up product photograph of a woman's hand with neat nails holding the product towards the camera, her "
     "face out of frame, the product in sharp focus, softly blurred bright background."),
    ("Model lifestyle",
     "Candid lifestyle photograph of a young woman using the product at home in a bright sunlit room, relaxed "
     "natural pose, warm daylight, authentic everyday feel, the product clearly visible."),
    ("Model outdoors",
     "Fashion campaign photograph, waist up, of a young woman on a sunny city street holding the product up at "
     "chest height towards the camera, confident smile, golden hour light, softly blurred street behind her."),
    ("Food overhead",
     "Overhead food photograph shot straight from above on a rustic wooden table, soft natural daylight from a "
     "window, fresh garnish and a few ingredients scattered around, appetising true colours."),
    ("Food dark & moody",
     "Dark and moody food photograph, a single soft side light on a deep charcoal backdrop, rich shadows, glossy "
     "highlights on the food, rustic props, intimate restaurant feel."),
    # art: these turn a whole-image edit into that kind of picture too
    ("Anime",
     "Anime illustration, clean confident line art, cel shading with soft gradients, vibrant colours, expressive "
     "eyes, glowing atmospheric light and a detailed hand-painted background."),
    ("Japanese ink",
     "Japanese ink illustration, bold black brush-pen linework and fine ink hatching on cream rice paper, a large "
     "textured red sun circle behind the subject, black brush-painted tree silhouettes, red used only as an "
     "accent, brushed calligraphy and a small red seal stamp."),
    ("Watercolour",
     "Watercolour painting on textured cold-press paper, soft transparent washes bleeding into each other, loose "
     "confident brushwork, white paper glowing through, delicate ink details."),
    ("Oil painting",
     "Classical oil painting on canvas, rich layered glazes, visible confident brushstrokes, warm painterly light "
     "falling from one side like an old master, deep soft shadows."),
    ("Pencil sketch",
     "Monochrome graphite pencil sketch on paper, black and grey only, fine cross-hatching, soft shading, "
     "hand-drawn lines."),
    ("Comic book",
     "Comic book art, bold black ink outlines, dramatic shading with halftone dots, flat vivid colours, strong "
     "directional light, energetic composition, clean art with no speech bubbles."),
    ("3D animation",
     "Stylized 3D animated film look, soft rounded shapes, appealing characters, subsurface-scattered skin, warm "
     "bounce lighting, rich colourful set, gentle depth of field."),
    ("Fantasy art",
     "Epic fantasy concept art, painterly digital illustration, sweeping dramatic sky, volumetric god rays, rich "
     "jewel-toned colours, intricate detail and a sense of wonder."),
    ("Neon cyberpunk",
     "Cyberpunk look, glowing magenta and cyan neon light, wet reflective surfaces, haze and volumetric beams, "
     "high-tech details, deep night shadows."),
    ("Toy figure",
     "Collectible vinyl toy figure standing on a plain round black base, smooth glossy plastic, slightly "
     "oversized head and simplified features, soft studio light, shallow depth of field."),
    ("Claymation",
     "Handmade claymation scene, everything sculpted from plasticine with visible fingerprints and soft rounded "
     "shapes, warm miniature set lighting, stop-motion charm."),
    ("Pixel art",
     "Detailed 16-bit pixel art, crisp square pixels, limited vibrant palette, clean outlines, classic video game "
     "look with simple dithered shading."),
    ("Stained glass",
     "Stained glass window artwork, jewel-toned glass pieces joined by dark lead lines, light shining through "
     "and glowing in the colours, gothic cathedral feel."),
    ("Retro 80s",
     "Retro 1980s synthwave artwork, glowing neon pink and electric blue, a sunset with horizontal stripes, a "
     "neon grid floor, chrome highlights and soft haze."),
    ("Surreal",
     "Surreal dreamlike artwork, impossible scale and floating objects, uncanny calm atmosphere, crisp detail, "
     "soft otherworldly light."),
    # eras: the clothes, setting and the photography of the time change; the people and the moment stay
    ("Victorian",
     "Victorian era photograph from the 1880s, clothing and hair of the time, sepia-toned glass plate "
     "print, soft edges and long-exposure stillness."),
    ("1920s",
     "1920s Jazz Age photograph, clothing and hair of the time, art deco details, black and white silver "
     "print, soft glow and fine grain."),
    ("1950s",
     "1950s photograph in saturated Kodachrome colour, clothing and hairstyles of the time, chrome and "
     "pastel details, bright optimistic daylight."),
    ("1970s",
     "1970s photograph, clothing and hair of the time, warm orange and brown tones, faded film colours, "
     "soft grain and natural light."),
    ("Future",
     "Futuristic photograph from the year 2090, sleek high-tech clothing, clean architecture with glowing "
     "panels, cool white and blue light, holographic details, polished sci-fi film look."),
]

PRODUCT = {"Studio white", "Hero shot", "Flat lay", "Lifestyle", "Luxury", "Podium", "Splash", "Nature",
           "Food overhead", "Food dark & moody", "Model studio", "Model close-up", "In hand", "Model lifestyle",
           "Model outdoors"}
MODEL = {"Model studio", "Model close-up", "In hand", "Model lifestyle", "Model outdoors"}
ART = {"Anime", "Japanese ink", "Watercolour", "Oil painting", "Pencil sketch", "Comic book", "3D animation",
       "Fantasy art", "Neon cyberpunk", "Toy figure", "Claymation", "Pixel art", "Stained glass", "Retro 80s",
       "Surreal"}
PORTRAIT = {"Editorial", "Corporate headshot", "Actor headshot", "Rembrandt light", "Beauty light",
            "Split light", "Rim light", "Window light", "Colour gels", "B&W portrait", "Colour backdrop"}
ERAS = {"Victorian", "1920s", "1950s", "1970s", "Future"}

# camera angles: on their own in the panel, and they add to the style that's on (angle first, then the look)
ANGLES = [
    ("Low angle", "Shot from a low angle looking up at the subject, making it look tall, heroic and powerful."),
    ("High angle", "Shot from a high angle, the camera above head height looking down at the subject."),
    ("Bird's-eye", "Bird's-eye view shot from directly overhead, looking straight down at the scene."),
    ("Worm's-eye", "Extreme worm's-eye view from ground level looking steeply up, with dramatic perspective."),
    ("Dutch tilt", "Dramatic Dutch angle, the camera tilted so the horizon runs diagonally across the frame."),
    ("Close-up", "Tight close-up shot with the camera very near, the subject filling the frame."),
    ("Side profile", "Shot from the side, showing the subject in a clean side profile."),
    ("From behind", "Shot from behind the subject, looking past them at what they face."),
    ("Wide shot", "Wide establishing shot from far away, the subject small within a large scene that shows the "
                  "whole place."),
    ("Fisheye", "Ultra-wide fisheye lens shot from up close, strongly curved edges, energetic action-sports feel."),
    ("Drone aerial", "Aerial drone photograph from high above at an angle, showing the subject and its "
                     "surroundings from the sky."),
]
KEEP_ANGLE = "Keep the same people, faces, clothes, objects and place, seen from this new camera angle."

HELP = ('Your styles. Add one to "styles" as {"name": "...", "text": "..."}: describe the look in a sentence or '
        "two (light, colours, medium; real camera or film names for photo looks). Use a built-in style's name "
        'to change its words. Put names in "hidden" to leave them out of the panel.')


def _write_blank(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"help": HELP, "styles": [], "hidden": []}, indent=2), encoding="utf-8")
    except OSError:
        pass


def _yours(path: Path) -> tuple[list[tuple[str, str]], set[str]]:
    """(your styles, hidden names) from the file; nothing if it's missing or can't be read."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], set()
    if isinstance(data, list):  # the first format: a plain list of styles
        data = {"styles": data}
    if not isinstance(data, dict):
        return [], set()
    mine = [(str(d["name"]).strip(), str(d["text"]).strip()) for d in data.get("styles") or []
            if isinstance(d, dict) and str(d.get("name", "")).strip() and str(d.get("text", "")).strip()]
    hidden = {n.strip() for n in data.get("hidden") or [] if isinstance(n, str)}
    return mine, hidden


def load(path: Path | None = None) -> list[tuple[str, str]]:
    """The built-in styles (with your changes, minus hidden ones), then yours. Creates a blank file to edit."""
    path = Path(path or STYLES_FILE)
    if not path.exists():
        _write_blank(path)
    mine, hidden = _yours(path)
    changed = dict(mine)
    names = {n for n, _ in DEFAULT_STYLES}
    built = [(n, changed.get(n, t)) for n, t in DEFAULT_STYLES if n not in hidden]
    return built + [(n, t) for n, t in mine if n not in names and n not in hidden]


def keep_for(name: str) -> str:
    """What a restyle with nothing typed keeps: the product, the dish, the room, or the people and framing."""
    if name in KEEPS:
        return KEEPS[name]
    return KEEP_PRODUCT if name in PRODUCT else KEEP_ERA if name in ERAS else KEEP_FACE if name in PORTRAIT \
        else KEEP


def angle_text(name: str) -> str:
    return dict(ANGLES).get(name, "") if name else ""


def text_for(name: str, path: Path | None = None) -> str:
    return dict(load(path)).get(name, "") if name else ""


def era_edit(text: str) -> str:
    """An era applied to a photo. Said as "change only the era, add nobody": the plain description made Klein
    add a partner in period dress next to a man standing alone."""
    return ("Change only the era of this photo and add nobody: dress the same person or people in the clothing of "
            f"the time and make it look like {text[:1].lower() + text[1:]}")


def styled(prompt: str, name: str, path: Path | None = None, restyle: bool = False, edit: bool = False,
           angle: str = "") -> str:
    """The style's sentence, then the prompt. First, because the look then holds better (Movie poster stops
    lettering a title). No style: the prompt as it is. No prompt: nothing, or with `restyle` (a whole-image
    edit) the style plus what to keep, which just changes the look. `edit`: applied to a photo (the Eras say
    it their own way). `angle`: a camera angle's sentence goes first; restyling then keeps the people and
    place but not the framing."""
    prompt = prompt.strip()
    extra = text_for(name, path)
    if extra and (edit or restyle) and name in ERAS:
        extra = era_edit(extra)
    turn = angle_text(angle)
    look = " ".join(x for x in (turn, extra) if x)
    if not look:
        return prompt
    if not prompt:
        if not restyle:
            return ""
        keep = keep_for(name)
        if turn and keep in (KEEP, KEEP_ERA):  # those keep the framing, which the angle changes
            keep = KEEP_ANGLE
        return f"{look} {keep}"
    return f"{look} {prompt}"
