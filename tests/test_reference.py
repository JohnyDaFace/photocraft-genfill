"""Replace face with a photo: every use asks which photo, so a new person never gets the last one's face."""
from types import SimpleNamespace

from genfill.app import App


def _bar(picks):
    started = []
    bar = SimpleNamespace(reference="C:/photos/anna.jpg", started=started)

    def choose(title=None):
        pick = picks.pop(0)
        if pick:
            bar.reference = pick
        return bool(pick)
    bar.choose_reference = choose
    bar.start = lambda job, **kw: started.append((job, kw, bar.reference))
    return bar


def test_asks_for_a_photo_even_when_one_was_used_before():
    bar = _bar(["C:/photos/ben.jpg"])
    App._replace_face_with_photo(bar)
    assert bar.started == [("faces", {"choice": "reference"}, "C:/photos/ben.jpg")]


def test_cancelling_the_photo_does_nothing():
    bar = _bar([""])
    App._replace_face_with_photo(bar)
    assert bar.started == []
