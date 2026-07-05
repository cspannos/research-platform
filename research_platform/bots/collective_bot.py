from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from projects.collective.publish.config import get_collective_settings
from projects.collective.workers.jobs import collective_export_job, collective_publish_job
from research_platform.core.config import get_settings
from research_platform.core.logging import configure_logging, get_logger
from research_platform.workers.base import get_queue

logger = get_logger(__name__)


def _authorized(update: Update, allowed: set[int]) -> bool:
    user = update.effective_user
    if not user:
        return False
    if not allowed:
        return False
    return user.id in allowed


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not _authorized(update, settings.allowed_user_ids):
        await update.message.reply_text("Unauthorized.")
        return

    collective = get_collective_settings()
    publish_state = "enabled" if collective.publish_enabled else "disabled (export-only)"
    await update.message.reply_text(
        "Collective bot online.\n"
        f"Publish: {publish_state}\n"
        "Commands:\n"
        "  /export <slug> | <title> | <markdown body>\n"
        "  /publish <slug> | <title> | <markdown body>\n"
        "Use | as delimiter. Publish requires COLLECTIVE_PUBLISH_ENABLED=true."
    )


def _parse_pipe_args(args: list[str]) -> tuple[str, str, str] | None:
    joined = " ".join(args).strip()
    parts = [part.strip() for part in joined.split("|")]
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1], parts[2]


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not _authorized(update, settings.allowed_user_ids):
        await update.message.reply_text("Unauthorized.")
        return

    parsed = _parse_pipe_args(context.args or [])
    if not parsed:
        await update.message.reply_text("Usage: /export slug | title | markdown body")
        return

    slug, title, body = parsed
    queue = get_queue(settings.tenant_id)
    job = queue.enqueue(collective_export_job, slug=slug, title=title, body_markdown=body)
    await update.message.reply_text(f"Export queued ({job.id}). Check data/collective/exports on server.")


async def publish_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not _authorized(update, settings.allowed_user_ids):
        await update.message.reply_text("Unauthorized.")
        return

    collective = get_collective_settings()
    if not collective.publish_enabled:
        await update.message.reply_text(
            "Automated publish is disabled. Use /export and publish manually from anon account."
        )
        return

    parsed = _parse_pipe_args(context.args or [])
    if not parsed:
        await update.message.reply_text("Usage: /publish slug | title | markdown body")
        return

    slug, title, body = parsed
    queue = get_queue(settings.tenant_id)
    job = queue.enqueue(collective_publish_job, slug=slug, title=title, body_markdown=body)
    await update.message.reply_text(f"Publish queued ({job.id}) → {collective.github_repo}")


async def echo_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if _authorized(update, settings.allowed_user_ids):
        return
    await update.message.reply_text("Unauthorized.")


def main() -> None:
    settings = get_settings()
    configure_logging(f"bot-{settings.tenant_id}", settings.tenant_id)

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for bot-collective")

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CommandHandler("publish", publish_cmd))
    app.add_handler(MessageHandler(filters.ALL, echo_unauthorized))

    logger.info("collective_bot_starting", tenant=settings.tenant_id)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
