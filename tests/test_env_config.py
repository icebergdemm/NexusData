from pathlib import Path

from config import update_env_file


def test_update_env_file_preserves_unrelated_lines_and_creates_backup(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# keep this comment\n"
        "DATABASE_URL=mysql://unchanged\n"
        "DEEPGRAM_API_KEY=old-value\n"
        "\n"
        "CUSTOM_SETTING=keep-me\n",
        encoding="utf-8",
    )

    backup = update_env_file(env_path, {"DEEPGRAM_API_KEY": "new-value"})

    assert backup is not None and backup.exists()
    assert env_path.read_text(encoding="utf-8") == (
        "# keep this comment\n"
        "DATABASE_URL=mysql://unchanged\n"
        "DEEPGRAM_API_KEY=new-value\n"
        "\n"
        "CUSTOM_SETTING=keep-me\n"
    )
    assert "DEEPGRAM_API_KEY=old-value" in backup.read_text(encoding="utf-8")
