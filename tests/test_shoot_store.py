from PIL import Image

from genfill import results_store, shoot_store
from genfill.shoot_store import ShootRun


def _run():
    return ShootRun("Luxury dark", "product", "marble", [("hero", "eye level"), ("macro", "cap")],
                    "1:1", (1440, 1440))


def test_a_run_round_trips(tmp_path):
    rid = shoot_store.save_run(tmp_path, _run(), Image.new("RGB", (64, 64), "red"), Image.new("RGB", (80, 80), "blue"))
    run, subject, hero = shoot_store.load_run(tmp_path, rid)
    assert run.shots == [("hero", "eye level"), ("macro", "cap")] and run.size == (1440, 1440)
    assert subject.size == (64, 64) and hero.getpixel((0, 0)) == (0, 0, 255)


def test_missing_or_broken_runs_load_as_none(tmp_path):
    assert shoot_store.load_run(tmp_path, "0123456789abcdef") is None
    rid = shoot_store.save_run(tmp_path, _run(), Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8)))
    (tmp_path / rid / "shoot.json").write_text("{")
    assert shoot_store.load_run(tmp_path, rid) is None


def test_only_the_newest_runs_are_kept(tmp_path):
    ids = [shoot_store.save_run(tmp_path, _run(), Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8)), keep=2)
           for _ in range(3)]
    assert shoot_store.load_run(tmp_path, ids[0]) is None and shoot_store.load_run(tmp_path, ids[2]) is not None


def test_the_tag_round_trips_and_is_not_a_results_tag():
    p = shoot_store.tag_packet("0123456789abcdef", 4)
    assert shoot_store.tag_of(p) == ("0123456789abcdef", 4)
    assert results_store.tag_of(p) is None and shoot_store.tag_of(results_store.tag_packet("0123456789abcdef")) is None
    assert shoot_store.tag_of(None) is None
