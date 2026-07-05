from __future__ import annotations

from projects.collective.publish.export import export_draft
from projects.collective.publish.github import GitHubPublishError, publish_file
from research_platform.core.logging import get_logger

logger = get_logger(__name__)


def collective_export_job(slug: str, title: str, body_markdown: str) -> dict[str, str]:
    """Export draft to local bundle — safe default before automated publish."""
    return export_draft(slug=slug, title=title, body_markdown=body_markdown)


def collective_publish_job(
    slug: str,
    title: str,
    body_markdown: str,
    *,
    publish_path: str | None = None,
) -> dict[str, str]:
    """
    Publish approved content to the anonymous GitHub repo.

    Requires COLLECTIVE_PUBLISH_ENABLED=true and secrets in collective.env.
    """
    path = publish_path or f"posts/{slug}.md"
    content = f"# {title}\n\n{body_markdown}\n"
    message = f"publish: {title} ({slug})"

    try:
        result = publish_file(path=path, content=content, message=message)
    except GitHubPublishError as exc:
        logger.warning("collective_publish_failed", slug=slug, error=str(exc))
        raise

    return {
        "slug": slug,
        "repo": result.repo,
        "path": result.path,
        "commit_sha": result.commit_sha,
        "html_url": result.html_url,
    }
