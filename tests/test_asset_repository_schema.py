from pet_harness.asset.asset_models import AssetJob, GeneratedAsset
from pet_harness.asset.asset_repository import AssetRepository
from pet_harness.storage.sqlite_store import SQLiteStore


def test_asset_tables_are_idempotent_and_version_assets(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    store.initialize()
    repo = AssetRepository(store)
    job = repo.create_job(AssetJob("miku", "variant_png", "development", "same"))
    assert repo.create_job(AssetJob("miku", "variant_png", "development", "same")).job_id == job.job_id
    first = repo.save_asset(GeneratedAsset("miku", "character_variant_png", "development", "a.png", "a.png", "image/png", "one", job.job_id, reward_id="reward"))
    second = repo.save_asset(GeneratedAsset("miku", "character_variant_png", "development", "b.png", "b.png", "image/png", "two", job.job_id, reward_id="reward"))
    assert (first["version"], second["version"]) == (1, 2)
    assert [item["file_path"] for item in store.list_character_assets("miku")] == ["b.png"]
