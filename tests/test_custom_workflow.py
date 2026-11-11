"""Your own ComfyUI workflow: reading the normal save (with subgraphs), finding its slots, running it."""
import json
import threading

import pytest
from PIL import Image

from genfill import custom_workflow as cw, settings as settings_file
from genfill.pipeline import Job, UserFacingError, _candidates
from genfill.settings import Settings

INFO = {  # what ComfyUI's /object_info says about the nodes used here
    "LoadImage": {"input": {"required": {"image": [["a.png"], {"image_upload": True}]}}},
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {"multiline": True}], "clip": ["CLIP"]}}},
    "KSampler": {"input": {"required": {
        "model": ["MODEL"], "seed": ["INT", {"control_after_generate": True}], "steps": ["INT", {}],
        "cfg": ["FLOAT", {}], "sampler_name": [["euler"]], "scheduler": [["simple"]], "positive": ["CONDITIONING"],
        "negative": ["CONDITIONING"], "latent_image": ["LATENT"], "denoise": ["FLOAT", {}]}}},
    "EmptyLatentImage": {"input": {"required": {"width": ["INT", {}], "height": ["INT", {}],
                                                "batch_size": ["INT", {}]}}},
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["m.safetensors"]]}}},
    "VAEDecode": {"input": {"required": {"samples": ["LATENT"], "vae": ["VAE"]}}},
    "SaveImage": {"input": {"required": {"images": ["IMAGE"], "filename_prefix": ["STRING", {}]}}},
    "PreviewImage": {"input": {"required": {"images": ["IMAGE"]}}},
}


def node(nid, kind, widgets=(), inputs=(), outputs=(), title=None, mode=0):
    return {"id": nid, "type": kind, "mode": mode, "widgets_values": list(widgets), "title": title,
            "inputs": [{"name": n, "type": t, "link": l} for n, t, l in inputs],
            "outputs": [{"name": t, "type": t} for t in outputs]}


def text_to_image():
    """A normal save: checkpoint -> two prompts -> sampler -> decode -> save, with a reroute, a note, a muted
    preview and the seed's "randomize"."""
    return {
        "nodes": [
            node(1, "CheckpointLoaderSimple", ["m.safetensors"], outputs=("MODEL", "CLIP", "VAE")),
            node(2, "CLIPTextEncode", ["a cat"], [("clip", "CLIP", 1)], ("CONDITIONING",), "Positive"),
            node(3, "CLIPTextEncode", ["blurry"], [("clip", "CLIP", 2)], ("CONDITIONING",), "Negative"),
            node(4, "EmptyLatentImage", [512, 512, 1], outputs=("LATENT",)),
            node(5, "KSampler", [42, "randomize", 20, 7.0, "euler", "simple", 1.0],
                 [("model", "MODEL", 3), ("positive", "CONDITIONING", 4), ("negative", "CONDITIONING", 5),
                  ("latent_image", "LATENT", 6)], ("LATENT",)),
            node(6, "VAEDecode", [], [("samples", "LATENT", 7), ("vae", "VAE", 8)], ("IMAGE",)),
            node(7, "Reroute", [], [("", "*", 9)], ("IMAGE",)),
            node(8, "SaveImage", ["out"], [("images", "IMAGE", 10)]),
            node(9, "Note", ["just a note"]),
            node(10, "PreviewImage", [], [("images", "IMAGE", 11)], mode=2),
        ],
        "links": [[1, 1, 1, 2, 0, "CLIP"], [2, 1, 1, 3, 0, "CLIP"], [3, 1, 0, 5, 0, "MODEL"],
                  [4, 2, 0, 5, 1, "CONDITIONING"], [5, 3, 0, 5, 2, "CONDITIONING"], [6, 4, 0, 5, 3, "LATENT"],
                  [7, 5, 0, 6, 0, "LATENT"], [8, 1, 2, 6, 1, "VAE"], [9, 6, 0, 7, 0, "IMAGE"],
                  [10, 7, 0, 8, 0, "IMAGE"], [11, 6, 0, 10, 0, "IMAGE"]],
    }


def test_a_normal_save_becomes_what_comfyui_runs():
    api = cw.to_api(text_to_image(), INFO)
    assert set(api) == {"1", "2", "3", "4", "5", "6", "8"}  # no note, reroute or muted preview
    k = api["5"]["inputs"]
    assert k["seed"] == 42 and k["steps"] == 20 and k["cfg"] == 7.0 and k["sampler_name"] == "euler"  # past "randomize"
    assert k["positive"] == ["2", 0] and k["model"] == ["1", 0]
    assert api["8"]["inputs"]["images"] == ["6", 0]  # through the reroute
    assert api["2"]["inputs"] == {"text": "a cat", "clip": ["1", 1]}


def test_slots_are_found_and_the_negative_prompt_is_not_the_prompt():
    api = cw.to_api(text_to_image(), INFO)
    found = cw.detect(api)
    assert found["prompt"] == "2" and found["result"] == "8" and found["picture"] is None
    assert cw.problems(found) == []


def test_an_api_export_is_taken_as_it_is():
    api = cw.to_api(text_to_image(), INFO)
    assert cw.to_api(api, {}) == api


def test_missing_nodes_are_named():
    data = text_to_image()
    data["nodes"].append(node(11, "SuperUpscaler9000", [], outputs=("IMAGE",)))
    with pytest.raises(cw.WorkflowError, match="SuperUpscaler9000"):
        cw.to_api(data, INFO)


def test_subgraphs_are_opened_up():
    """The sampler and the prompt live in a subgraph; its prompt is a widget shown on the subgraph node."""
    inner = {
        "id": "sg-1", "name": "Generate", "inputs": [{"name": "model", "type": "MODEL"}, {"name": "clip", "type": "CLIP"},
                                                      {"name": "text", "type": "STRING"}],
        "outputs": [{"name": "LATENT", "type": "LATENT"}],
        "nodes": [node(1, "CLIPTextEncode", ["inner default"], [("clip", "CLIP", 21), ("text", "STRING", 23)],
                       ("CONDITIONING",)),
                  node(2, "EmptyLatentImage", [512, 512, 1], outputs=("LATENT",)),
                  node(3, "KSampler", [1, "fixed", 4, 1.0, "euler", "simple", 1.0],
                       [("model", "MODEL", 20), ("positive", "CONDITIONING", 22), ("negative", "CONDITIONING", 22),
                        ("latent_image", "LATENT", 24)], ("LATENT",))],
        "links": [{"id": 20, "origin_id": -10, "origin_slot": 0, "target_id": 3, "target_slot": 0},
                  {"id": 21, "origin_id": -10, "origin_slot": 1, "target_id": 1, "target_slot": 0},
                  {"id": 22, "origin_id": 1, "origin_slot": 0, "target_id": 3, "target_slot": 1},
                  {"id": 23, "origin_id": -10, "origin_slot": 2, "target_id": 1, "target_slot": 1},
                  {"id": 24, "origin_id": 2, "origin_slot": 0, "target_id": 3, "target_slot": 3},
                  {"id": 25, "origin_id": 3, "origin_slot": 0, "target_id": -20, "target_slot": 0}],
    }
    sub = node(5, "sg-1", ["a dog"], [("model", "MODEL", 1), ("clip", "CLIP", 2), ("text", "STRING", None)],
               ("LATENT",))
    sub["inputs"][2]["widget"] = {"name": "text"}
    data = {"nodes": [node(1, "CheckpointLoaderSimple", ["m.safetensors"], outputs=("MODEL", "CLIP", "VAE")), sub,
                      node(6, "VAEDecode", [], [("samples", "LATENT", 3), ("vae", "VAE", 4)], ("IMAGE",)),
                      node(8, "SaveImage", ["out"], [("images", "IMAGE", 5)])],
            "links": [[1, 1, 0, 5, 0, "MODEL"], [2, 1, 1, 5, 1, "CLIP"], [3, 5, 0, 6, 0, "LATENT"],
                      [4, 1, 2, 6, 1, "VAE"], [5, 6, 0, 8, 0, "IMAGE"]],
            "definitions": {"subgraphs": [inner]}}
    api = cw.to_api(data, INFO)
    assert api["6"]["inputs"]["samples"] == ["5:3", 0]      # out of the subgraph to the sampler inside
    assert api["5:3"]["inputs"]["model"] == ["1", 0]        # into the subgraph from the checkpoint outside
    assert api["5:1"]["inputs"]["text"] == "a dog"          # the value set on the subgraph node
    assert cw.detect(api)["prompt"] == "5:1"


def test_prepare_puts_in_the_picture_prompt_seed_and_size():
    api = cw.to_api(text_to_image(), INFO)
    api["20"] = {"class_type": "LoadImage", "inputs": {"image": "a.png"}, "_meta": {"title": "Load Image"}}
    slots = {**cw.detect(api), "picture": "20"}
    wf = cw.prepare(api, slots, "a red car", 7, picture="genfill/src.png", size=(768, 512))
    assert wf["2"]["inputs"]["text"] == "a red car" and wf["3"]["inputs"]["text"] == "blurry"
    assert wf["20"]["inputs"]["image"] == "genfill/src.png"
    assert wf["4"]["inputs"]["width"] == 768 and wf["4"]["inputs"]["height"] == 512
    assert wf["5"]["inputs"]["seed"] != 42
    assert api["2"]["inputs"]["text"] == "a cat"  # the workflow itself isn't changed


def test_a_picture_that_carries_the_mask_is_noticed():
    api = {"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
           "2": {"class_type": "SetLatentNoiseMask", "inputs": {"mask": ["1", 1]}}}
    assert cw.picture_carries_mask(api, {"picture": "1"})
    assert not cw.picture_carries_mask(api, {"picture": None})


# -- in Generate and Create -------------------------------------------------------------------------
class OwnComfy:
    def __init__(self, color=(255, 0, 0)):
        self.color, self.wfs, self.nodes, self.uploads = color, [], [], []
    def check(self): return {}
    def upload(self, img, name):
        self.uploads.append((name, img.mode))
        return "genfill/" + name
    def run(self, wf, on_progress, cancel, node=None):
        """The picture's blue, with a red square in the middle (and blue again where it isn't asked to draw)."""
        self.wfs.append(wf)
        self.nodes.append(node)
        out = Image.new("RGB", (100, 100), (0, 0, 255))
        out.paste(self.color, (40, 40, 60, 60))
        return out


@pytest.fixture
def own(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_file, "CONFIG_DIR", tmp_path)
    api = cw.to_api(text_to_image(), INFO)
    api["20"] = {"class_type": "LoadImage", "inputs": {"image": "a.png"}, "_meta": {"title": "Load Image"}}
    cw.save(api, tmp_path)
    return Settings(custom_workflow="C:/wf/mine.json", custom_slots={**cw.detect(api), "picture": "20"})


def test_create_runs_your_workflow(own):
    comfy = OwnComfy()
    job = Job(Image.new("RGBA", (200, 100), "white"), None, None, "a lighthouse", create=True, custom=True)
    status = []
    out = _candidates(job, own, status.append, lambda f: None, threading.Event(), comfy, 2)
    assert len(out) == 2 and out[0].size == (200, 100)
    assert comfy.nodes == ["8", "8"] and comfy.wfs[0]["2"]["inputs"]["text"] == "a lighthouse"
    assert any("your workflow" in s for s in status)


def test_create_reference_uses_first_image_input_of_custom_workflow(own):
    class CaptureComfy(OwnComfy):
        def upload(self, img, name):
            self.uploads.append((name, img.copy()))
            return "genfill/" + name

    own.custom_native_size = True
    reference = Image.new("RGB", (120, 80), "red")
    job = Job(Image.new("RGBA", (200, 100), "white"), None, None, "restyle",
              reference=reference, create=True, custom=True)
    comfy = CaptureComfy()
    _candidates(job, own, lambda _: None, lambda _: None, threading.Event(), comfy, 1)
    assert comfy.wfs[0]["20"]["inputs"]["image"] == "genfill/genfill_src.png"
    assert len(comfy.uploads) == 1
    assert comfy.uploads[0][1].size == (120, 80)
    assert comfy.uploads[0][1].getpixel((0, 0)) == (255, 0, 0)


def test_custom_create_rejects_second_image_without_second_input(own):
    job = Job(Image.new("RGBA", (200, 100), "white"), None, None, "use both images",
              reference=Image.new("RGB", (120, 80), "red"), create=True, custom=True,
              second_reference=Image.new("RGB", (120, 80), "blue"))
    with pytest.raises(UserFacingError, match="only one image input"):
        _candidates(job, own, lambda _: None, lambda _: None, threading.Event(), OwnComfy(), 1)


def test_generate_reference_uses_first_input_when_workflow_has_only_one(own):
    class CaptureComfy(OwnComfy):
        def upload(self, img, name):
            self.uploads.append((name, img.copy()))
            return "genfill/" + name

    own.custom_native_size = True
    job = Job(Image.new("RGBA", (200, 100), "white"), None, None, "restyle",
              reference=Image.new("RGB", (120, 80), "red"), custom=True)
    comfy = CaptureComfy()
    _candidates(job, own, lambda _: None, lambda _: None, threading.Event(), comfy, 1)
    assert len(comfy.uploads) == 1
    assert comfy.uploads[0][1].size == (120, 80)
    assert comfy.uploads[0][1].getpixel((0, 0)) == (255, 0, 0)


def test_a_fill_with_your_workflow_changes_only_the_selection(own):
    comfy = OwnComfy((255, 0, 0))
    base = Image.new("RGBA", (100, 100), (0, 0, 255, 255))
    mask = Image.new("L", (100, 100), 0)
    mask.paste(255, (40, 40, 60, 60))
    job = Job(base, mask, (40, 40, 60, 60), "a red square", custom=True)
    out = _candidates(job, own, lambda s: None, lambda f: None, threading.Event(), comfy, 1)[0]
    assert out.getpixel((5, 5))[:3] == (0, 0, 255)          # outside: the picture itself
    assert out.getpixel((50, 50))[0] > 80                   # inside: what the workflow drew (red, blended in)
    assert comfy.wfs[0]["20"]["inputs"]["image"] == "genfill/genfill_src.png"


def test_expand_and_remove_keep_genfills_own_workflow(own):
    from tests.test_pipeline import FakeComfy
    comfy = FakeComfy()
    base = Image.new("RGBA", (64, 64), "white")
    mask = Image.new("L", (64, 64), 0)
    mask.paste(255, (10, 10, 30, 30))
    _candidates(Job(base, mask, (10, 10, 30, 30), "x"), own, lambda s: None, lambda f: None, threading.Event(),
                comfy, 1)
    assert "20" not in comfy.wfs[0]  # GenFill's own fill workflow


def test_a_lost_workflow_file_says_so(own, tmp_path):
    cw.file_path(tmp_path).unlink()
    job = Job(Image.new("RGBA", (64, 64), "white"), None, None, "x", create=True, custom=True)
    with pytest.raises(UserFacingError, match="mine.json"):
        _candidates(job, own, lambda s: None, lambda f: None, threading.Event(), OwnComfy(), 1)


def test_settings_remember_the_workflow(tmp_path):
    s = Settings(custom_workflow="C:/wf/mine.json", custom_slots={"result": "8"},
                 custom_keep_seeds=True, custom_native_size=True, custom_result_nodes=["9"])
    s.save(tmp_path / "s.json")
    back = Settings.load(tmp_path / "s.json")
    assert back.custom_workflow == "C:/wf/mine.json" and back.custom_slots == {"result": "8"}
    assert back.custom_keep_seeds and back.custom_native_size and back.custom_result_nodes == ["9"]
    raw = json.loads((tmp_path / "s.json").read_text())
    raw["custom_slots"] = "junk"
    (tmp_path / "s.json").write_text(json.dumps(raw))
    assert Settings.load(tmp_path / "s.json").custom_slots == {}


def test_custom_workflow_can_keep_its_saved_seed():
    api = cw.to_api(text_to_image(), INFO)
    original = api["5"]["inputs"]["seed"]
    prepared = cw.prepare(api, cw.detect(api), "a red car", 7, keep_seeds=True)
    assert prepared["5"]["inputs"]["seed"] == original


def test_custom_workflow_can_return_selected_outputs_in_one_run(own):
    saved = cw.load_saved(settings_file.CONFIG_DIR)
    saved["9"] = {"class_type": "PreviewImage", "inputs": {"images": ["7", 0]}}
    cw.save(saved, settings_file.CONFIG_DIR)

    class MultiComfy(OwnComfy):
        def run_selected(self, wf, on_progress, cancel, nodes):
            self.wfs.append(wf)
            self.nodes.append(nodes)
            return [Image.new("RGB", (120, 80), color) for color in ("red", "blue")]

    comfy = MultiComfy()
    own.custom_result_nodes = ["8", "9"]
    base = Image.new("RGBA", (100, 100), "white")
    job = Job(base, None, None, "test", custom=True)
    images = _candidates(job, own, lambda s: None, lambda f: None, threading.Event(), comfy, 1)
    assert len(images) == 2
    assert [im.getpixel((0, 0))[:3] for im in images] == [(255, 0, 0), (0, 0, 255)]


def test_custom_workflow_can_preserve_generated_dimensions(own):
    class LargeComfy(OwnComfy):
        def run(self, wf, on_progress, cancel, node=None):
            return Image.new("RGB", (120, 80), "red")

    own.custom_native_size = True
    base = Image.new("RGBA", (100, 100), "white")
    job = Job(base, None, None, "test", custom=True)
    out = _candidates(job, own, lambda s: None, lambda f: None, threading.Event(), LargeComfy(), 1)[0]
    assert out.size == (120, 80)


def test_native_size_edit_opens_a_new_document(own, tmp_path):
    from tests.test_pipeline import FakePC
    from genfill.pipeline import generate, place

    class LargeComfy(OwnComfy):
        def run(self, wf, on_progress, cancel, node=None):
            return Image.new("RGB", (120, 80), "red")

    own.custom_native_size = True
    own.exchange_dir = str(tmp_path)
    base = Image.new("RGBA", (100, 100), "white")
    pc = FakePC(base, None)
    results = generate("change the background", own, lambda s: None, lambda f: None,
                       threading.Event(), pc=pc, comfy=LargeComfy(), count=1)
    assert results.new_document
    assert results.images[0].size == (120, 80)
    place(results, 0, own, pc=pc)
    assert len(pc.opened) == 1 and not pc.placed


def test_changing_main_result_refreshes_additional_output_choices():
    from types import SimpleNamespace
    from genfill.settings_dialog import SettingsDialog

    class Listbox:
        def __init__(self): self.items, self.selected = [], set()
        def delete(self, *_): self.items, self.selected = [], set()
        def insert(self, _, text): self.items.append(text)
        def selection_set(self, index): self.selected.add(index)
        def curselection(self): return sorted(self.selected)

    wf = {nid: {"class_type": "PreviewImage", "inputs": {"images": ["source", 0]}}
          for nid in ("raw", "matched", "third")}
    box = Listbox()
    state = SimpleNamespace(own_wf=wf, extra_results=box, extra_result_ids=[],
                            _slots=lambda: {"result": "matched"})
    state._extra_result_nodes = lambda: [state.extra_result_ids[i] for i in box.curselection()]
    SettingsDialog._refresh_extra_results(state, ["third"])
    assert state.extra_result_ids == ["raw", "third"]
    assert state._extra_result_nodes() == ["third"]
    state._slots = lambda: {"result": "raw"}
    SettingsDialog._refresh_extra_results(state)
    assert state.extra_result_ids == ["matched", "third"]
    assert state._extra_result_nodes() == ["third"]


def test_workflow_settings_use_image_roles_instead_of_node_numbers():
    from genfill.settings_dialog import SettingsDialog

    wf = {
        "168": {"class_type": "LoadImage", "inputs": {"image": "old.jpg"}},
        "173": {"class_type": "PreviewImage", "inputs": {"images": ["172", 0]}},
        "188": {"class_type": "PreviewImage", "inputs": {"images": ["187", 0]}},
        "187": {"class_type": "ColorMatchV2", "inputs": {"image_target": ["172", 0]}},
        "172": {"class_type": "VAEDecode", "inputs": {}},
    }
    assert SettingsDialog._workflow_option_label(wf, "picture", "168") == "Open image / bar Reference"
    assert SettingsDialog._workflow_option_label(wf, "result", "173") == "Generated image"
    assert SettingsDialog._workflow_option_label(wf, "result", "188") == "Color matched image"
    assert SettingsDialog._workflow_options(wf, "result", ["173", "188"])["Color matched image"] == "188"


def test_saved_workflows_have_separate_copies_and_settings_entries(tmp_path):
    first = cw.to_api(text_to_image(), INFO)
    second = json.loads(json.dumps(first))
    second["2"]["inputs"]["text"] = "a river"
    cw.save(first, tmp_path, "first")
    cw.save(second, tmp_path, "second")
    assert cw.load_saved(tmp_path, "first") == first
    assert cw.load_saved(tmp_path, "second") == second
    assert cw.load_saved(tmp_path) is None  # the active workflow copy is separate

    settings_path = tmp_path / "settings.json"
    s = Settings(saved_workflows=[
        {"id": "first", "name": "Portrait", "source": "C:/portrait.json", "slots": {"result": "8"}},
        {"id": "second", "name": "Landscape", "source": "C:/landscape.json", "slots": {"result": "8"}},
    ], active_workflow_id="second")
    s.save(settings_path)
    loaded = Settings.load(settings_path)
    assert loaded.active_workflow_id == "second"
    assert [entry["name"] for entry in loaded.saved_workflows] == ["Portrait", "Landscape"]
