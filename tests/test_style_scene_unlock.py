from types import SimpleNamespace

from pet_harness.ui.character_ui_service import CharacterUiService


def _service(tmp_path):
    service = CharacterUiService.__new__(CharacterUiService)
    service._router = SimpleNamespace(
        load_profile=lambda _character_id: (
            SimpleNamespace(sqlite_path=tmp_path / "state.db"),
            None,
        )
    )
    return service


def test_style_unlock_flag_defaults_off_and_persists(tmp_path):
    service = _service(tmp_path)

    assert service.get_style_unlock_all("pet") is False
    assert service.set_style_unlock_all("pet", True) is True
    assert _service(tmp_path).get_style_unlock_all("pet") is True

