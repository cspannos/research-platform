from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from projects.collective.publish.config import get_collective_settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)


def export_draft(
    slug: str,
    title: str,
    body_markdown: str,
    *,
    export_dir: str | None = None,
) -> dict[str, str]:
    """Write a draft bundle to disk for manual review before publish."""
    cfg = get_collective_settings()
    base = Path(export_dir or cfg.export_dir)
    base.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in slug.lower())[:64]
    bundle_dir = base / f"{timestamp}-{safe_slug}"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    md_path = bundle_dir / "draft.md"
    meta_path = bundle_dir / "meta.json"

    md_path.write_text(f"# {title}\n\n{body_markdown}\n", encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "slug": safe_slug,
                "title": title,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "publish_path": f"posts/{safe_slug}.md",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info("collective_export_completed", slug=safe_slug, bundle=str(bundle_dir))
    return {
        "slug": safe_slug,
        "bundle_dir": str(bundle_dir),
        "markdown_path": str(md_path),
        "suggested_publish_path": f"posts/{safe_slug}.md",
    }
