from pet_harness.app.secret_masking import SecretMasker, load_project_env


def test_env_loader_expands_prior_values_and_masker_redacts_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=abcdefgh\nURL=https://x/${API_KEY}\n", encoding="utf-8")

    environment = load_project_env(env_file)
    masked = SecretMasker(environment).payload({"api_key": "abcdefgh", "message": "token abcdefgh"})

    assert environment["URL"] == "https://x/abcdefgh"
    assert masked == {"api_key": "***", "message": "token ***"}
