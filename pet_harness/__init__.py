"""UI-agnostic Pet Harness Engine package."""

__all__ = ["PetHarnessEngine"]


def __getattr__(name: str):
    if name == "PetHarnessEngine":
        from pet_harness.engine.harness_engine import PetHarnessEngine

        return PetHarnessEngine
    raise AttributeError(name)
