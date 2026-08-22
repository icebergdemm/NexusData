from types import SimpleNamespace
from unittest.mock import patch

import services_git_update


def _settings(*, dist_branch: str = "frontend-dist", restart: bool = False):
    return SimpleNamespace(
        git_update_remote="origin",
        git_update_branch="main",
        git_update_token="",
        git_update_token_username="x-access-token",
        git_update_build_frontend=True,
        git_update_frontend_dist_branch=dist_branch,
        git_update_restart_backend=restart,
    )


def _unchanged_git_run(cmd, _cwd, _timeout, env=None):
    del env
    if cmd[:3] == ["git", "status", "--porcelain"]:
        return 0, ""
    if cmd[:3] == ["git", "rev-parse", "HEAD"]:
        return 0, "26b5631"
    if "pull" in cmd:
        return 0, "Already up to date."
    raise AssertionError(f"Unexpected command: {cmd}")


def test_update_refreshes_prebuilt_frontend_when_main_is_unchanged():
    output: list[str] = []

    with (
        patch.object(services_git_update, "get_settings", return_value=_settings()),
        patch.object(services_git_update, "repo_dir", return_value="/repo"),
        patch.object(services_git_update, "check_repo", return_value=None),
        patch.object(services_git_update, "current_revision", return_value="26b5631 — current"),
        patch.object(services_git_update, "remote_url", return_value="https://example.test/repo.git"),
        patch.object(services_git_update, "_git_auth", return_value=([], {})),
        patch.object(services_git_update, "_auth_config", return_value=None),
        patch.object(services_git_update, "_run", side_effect=_unchanged_git_run),
        patch.object(services_git_update, "_fetch_prebuilt_frontend", return_value=True) as fetch_dist,
        patch.object(services_git_update, "_restart_backend") as restart_backend,
    ):
        assert services_git_update.run_update(output.append) is True

    fetch_dist.assert_called_once_with(output.append, "/repo", "origin", "frontend-dist")
    restart_backend.assert_not_called()
    assert "Обновлений в коде нет — main уже актуален." in output
    assert "✓ Код и фронтенд актуальны." in output


def test_update_restart_is_honoured_when_main_is_unchanged():
    output: list[str] = []

    with (
        patch.object(services_git_update, "get_settings", return_value=_settings(dist_branch="")),
        patch.object(services_git_update, "repo_dir", return_value="/repo"),
        patch.object(services_git_update, "check_repo", return_value=None),
        patch.object(services_git_update, "current_revision", return_value="26b5631 — current"),
        patch.object(services_git_update, "remote_url", return_value="https://example.test/repo.git"),
        patch.object(services_git_update, "_git_auth", return_value=([], {})),
        patch.object(services_git_update, "_auth_config", return_value=None),
        patch.object(services_git_update, "_run", side_effect=_unchanged_git_run),
        patch.object(services_git_update, "_fetch_prebuilt_frontend") as fetch_dist,
        patch.object(services_git_update, "_restart_backend") as restart_backend,
    ):
        assert services_git_update.run_update(output.append, force_restart=True) is True

    fetch_dist.assert_not_called()
    restart_backend.assert_called_once_with(output.append)
    assert "→ Перезапуск backend…" in output
