from pet_harness.character.router import CharacterRouter


class _Registry:
    def load_character(self, character_id):
        return type("Profile", (), {"character_id": character_id, "name": character_id, "motions": {}, "idle_pool": [], "background_image": "", "layout": {}, "voice_id_env_key": ""})()


def test_switch_retires_previous_engine_before_opening_next_memory_store(monkeypatch):
    events = []

    class FakeEngine:
        def __init__(self, *, character_id, **_kwargs):
            self.character_profile = _Registry().load_character(character_id)
            self.skills = []

        def shutdown(self):
            events.append("shutdown")

    monkeypatch.setattr("pet_harness.character.router.PetHarnessEngine", FakeEngine)
    router = CharacterRouter(registry=_Registry(), memory_store_factory=lambda cid, _profile: events.append(f"store:{cid}") or object())
    router.switch_character("Choppr")
    events.clear()
    router.switch_character("miku")
    assert events == ["shutdown", "store:miku"]
