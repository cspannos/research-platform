from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from projects.collective.publish.config import CollectivePublishSettings
from projects.collective.publish.export import export_draft
from projects.collective.publish.github import GitHubPublishError, publish_file


@pytest.fixture
def collective_settings(tmp_path) -> CollectivePublishSettings:
    return CollectivePublishSettings(
        github_repo="anon-user/collective-site",
        git_user_name="Test Pseudonym",
        git_user_email="12345+anon@users.noreply.github.com",
        github_token="ghp_test_token",
        publish_enabled=True,
        export_dir=str(tmp_path / "exports"),
    )


def test_export_draft_writes_bundle(collective_settings: CollectivePublishSettings) -> None:
    result = export_draft(
        slug="hello-world",
        title="Hello",
        body_markdown="Body text",
        export_dir=collective_settings.export_dir,
    )
    assert result["slug"] == "hello-world"
    assert result["suggested_publish_path"] == "posts/hello-world.md"


def test_publish_disabled_raises(collective_settings: CollectivePublishSettings) -> None:
    disabled = collective_settings.model_copy(update={"publish_enabled": False})
    with pytest.raises(GitHubPublishError, match="disabled"):
        publish_file("posts/test.md", "# Test", "msg", settings=disabled)


def test_publish_file_success(collective_settings: CollectivePublishSettings) -> None:
    get_response = MagicMock()
    get_response.status_code = 404

    put_response = MagicMock()
    put_response.status_code = 201
    put_response.json.return_value = {
        "commit": {"sha": "abc123"},
        "content": {"html_url": "https://github.com/anon-user/collective-site/blob/main/posts/test.md"},
    }

    mock_client = MagicMock()
    mock_client.get.return_value = get_response
    mock_client.put.return_value = put_response
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("projects.collective.publish.github.httpx.Client", return_value=mock_client):
        result = publish_file("posts/test.md", "# Title\n\nBody", "publish: test", settings=collective_settings)

    assert result.repo == "anon-user/collective-site"
    assert result.path == "posts/test.md"
    assert result.commit_sha == "abc123"
    mock_client.put.assert_called_once()
    call_kwargs = mock_client.put.call_args
    assert "repos/anon-user/collective-site/contents/posts/test.md" in call_kwargs[0][0]
    assert call_kwargs[1]["json"]["author"]["email"] == "12345+anon@users.noreply.github.com"
