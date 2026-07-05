from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from projects.exoplanet.settings import load_targets
from projects.exoplanet.workers.jobs import (
    exoplanet_analyze_target_job,
    exoplanet_ingest_job,
    exoplanet_notify_telegram_job,
    exoplanet_review_summary_job,
    exoplanet_scan_job,
)
from research_platform.core.config import get_settings
from research_platform.core.logging import configure_logging, get_logger
from research_platform.workers.base import get_queue

logger = get_logger(__name__)


def _authorized(update: Update, allowed: set[int]) -> bool:
    user = update.effective_user
    return bool(user and allowed and user.id in allowed)


async def _guard(update: Update) -> bool:
    settings = get_settings()
    if _authorized(update, settings.allowed_user_ids):
        return True
    await update.message.reply_text("Unauthorized.")
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await update.message.reply_text(
        "Exoplanet citizen science bot\n"
        "Commands:\n"
        "  /targets — list curated targets\n"
        "  /ingest — fetch/cache light curves via MAST\n"
        "  /scan — analyze all cached targets\n"
        "  /analyze <slug> — analyze one target\n"
        "  /summaries — generate human-review summaries\n"
        "  /notify — send Telegram digest of pending candidates"
    )


async def targets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    rows = load_targets()
    if not rows:
        await update.message.reply_text("No targets configured.")
        return
    lines = [f"• {t.id} — {t.name} ({t.mission})" for t in rows]
    await update.message.reply_text("Curated targets:\n" + "\n".join(lines))


async def _enqueue(update: Update, job_name: str, func, *args, **kwargs) -> None:
    settings = get_settings()
    queue = get_queue(settings.tenant_id)
    job = queue.enqueue(func, *args, **kwargs)
    await update.message.reply_text(f"Queued {job_name} ({job.id}).")


async def ingest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await _enqueue(update, "ingest", exoplanet_ingest_job)


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await _enqueue(update, "scan", exoplanet_scan_job)


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    slug = " ".join(context.args).strip()
    if not slug:
        await update.message.reply_text("Usage: /analyze <target-slug>")
        return
    await _enqueue(update, "analyze", exoplanet_analyze_target_job, slug)


async def summaries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await _enqueue(update, "summaries", exoplanet_review_summary_job)


async def notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await _enqueue(update, "notify", exoplanet_notify_telegram_job)


async def echo_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if _authorized(update, settings.allowed_user_ids):
        return
    await update.message.reply_text("Unauthorized.")


def main() -> None:
    settings = get_settings()
    configure_logging(f"bot-{settings.tenant_id}", settings.tenant_id)
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for bot-exoplanet")

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("targets", targets))
    app.add_handler(CommandHandler("ingest", ingest))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("summaries", summaries))
    app.add_handler(CommandHandler("notify", notify))
    app.add_handler(MessageHandler(filters.ALL, echo_unauthorized))

    logger.info("exoplanet_bot_starting")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
