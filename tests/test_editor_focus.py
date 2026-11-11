from genfill.editor_focus import classify_editor


def test_editor_focus_identifies_only_editor_windows():
    assert classify_editor("Photoshop.exe", "Untitled-1") == "photoshop"
    assert classify_editor("PhotoCraft.exe", "Untitled - PhotoCraft") == "photocraft"
    assert classify_editor("artcraft-launcher.exe", "ArtCraft Launcher") is None
    assert classify_editor("GenFill-for-PhotoCraft.exe", "Generative Fill for PhotoCraft") is None
