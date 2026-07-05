from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from research_platform.core.config import get_settings
from research_platform.core.logging import configure_logging, get_logger
from research_platform.workers.jobs.demo import ping_job, summarize_text_job
from research_platform.workers.base import get_queue

logger = get_logger(__name__)


def _authorized(update: Update, allowed: set[int]) -> bool:
    user = update.effective_user
    if not user:
        return False
    if not allowed:
        logger.warning("telegram_allowlist_empty")
        return False
    return user.id in allowed


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not _authorized(update, settings.allowed_user_ids):
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text(
        "Research Platform demo bot online.\n"
        "Commands: /start /ping /summarize <text>"
    )


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not _authorized(update, settings.allowed_user_ids):
        await update.message.reply_text("Unauthorized.")
        return

    queue = get_queue(settings.tenant_id)
    job = queue.enqueue(ping_job, message="telegram-ping")
    await update.message.reply_text(f"Queued job {job.id}. Worker will process shortly.")


async def summarize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not _authorized(update, settings.allowed_user_ids):
        await update.message.reply_text("Unauthorized.")
        return

    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /summarize <text>")
        return

    queue = get_queue(settings.tenant_id)
    job = queue.enqueue(
        summarize_text_job,
        tenant_id=settings.tenant_id,
        subject="telegram-request",
        body=text,
    )
    await update.message.reply_text(f"Summary queued as job {job.id}.")


async def echo_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if _authorized(update, settings.allowed_user_ids):
        return
    await update.message.reply_text("Unauthorized.")


def main() -> None:
    settings = get_settings()
    configure_logging(f"bot-{settings.tenant_id}", settings.tenant_id)

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for bot-demo")

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("summarize", summarize))
    app.add_handler(MessageHandler(filters.ALL, echo_unauthorized))

    logger.info("bot_starting", mode=settings.telegram_mode, tenant=settings.tenant_id)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
