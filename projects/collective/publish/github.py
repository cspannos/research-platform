from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx

from projects.collective.publish.config import CollectivePublishSettings, get_collective_settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubPublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishResult:
    repo: str
    path: str
    commit_sha: str
    html_url: str


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_existing_sha(client: httpx.Client, repo: str, path: str, token: str) -> str | None:
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    response = client.get(url, headers=_headers(token))
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise GitHubPublishError(f"GitHub GET contents failed ({response.status_code}): {response.text}")
    payload = response.json()
    if isinstance(payload, list):
        raise GitHubPublishError(f"Path {path} is a directory, expected file")
    return payload.get("sha")


def publish_file(
    path: str,
    content: str,
    message: str,
    *,
    settings: CollectivePublishSettings | None = None,
) -> PublishResult:
    """
    Publish a single file to the anon GitHub repo via Contents API.

    No local git clone, no shell — repo URL is fixed by COLLECTIVE_GITHUB_REPO.
    """
    cfg = settings or get_collective_settings()
    cfg.validate_for_publish()

    if not cfg.publish_enabled:
        raise GitHubPublishError("Collective publish is disabled (COLLECTIVE_PUBLISH_ENABLED=false)")

    repo = cfg.github_repo.strip()
    token = cfg.github_token.strip()
    normalized_path = path.lstrip("/")

    author = {
        "name": cfg.git_user_name.strip(),
        "email": cfg.git_user_email.strip(),
    }
    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "committer": author,
        "author": author,
    }

    with httpx.Client(timeout=30.0) as client:
        existing_sha = _get_existing_sha(client, repo, normalized_path, token)
        if existing_sha:
            body["sha"] = existing_sha

        url = f"{GITHUB_API}/repos/{repo}/contents/{normalized_path}"
        response = client.put(url, headers=_headers(token), json=body)

    if response.status_code not in (200, 201):
        raise GitHubPublishError(f"GitHub PUT contents failed ({response.status_code}): {response.text}")

    payload = response.json()
    commit = payload.get("commit", {})
    content_meta = payload.get("content", {})

    result = PublishResult(
        repo=repo,
        path=normalized_path,
        commit_sha=str(commit.get("sha", "")),
        html_url=str(content_meta.get("html_url", "")),
    )
    logger.info(
        "collective_publish_completed",
        repo=result.repo,
        path=result.path,
        commit_sha=result.commit_sha[:12] if result.commit_sha else "",
    )
    return result
