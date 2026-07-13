from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

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

# Keep Telegram handlers responsive; long MAST ingest can still finish within this window.
DEFAULT_JOB_TIMEOUT_S = 180.0


def _authorized(update: Update, allowed: set[int]) -> bool:
    user = update.effective_user
    return bool(user and allowed and user.id in allowed)


async def _deny(update: Update) -> None:
    """Reject with the caller's Telegram user id so allowlist setup is self-service."""
    user = update.effective_user
    user_id = user.id if user else "unknown"
    logger.warning("telegram_unauthorized", telegram_user_id=user_id)
    if update.message:
        await update.message.reply_text(
            f"Unauthorized.\n"
            f"Your Telegram user id is: {user_id}\n"
            f"Set EXOPLANET_TELEGRAM_ALLOWED_USER_IDS={user_id} in .env "
            f"and recreate bot-exoplanet."
        )


async def _guard(update: Update) -> bool:
    settings = get_settings()
    if _authorized(update, settings.allowed_user_ids):
        return True
    await _deny(update)
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
        "  /notify — send Telegram digest of pending candidates\n"
        "  /ask [candidate_id] <question> — exoplanet expert (same LLM as review Enrich)\n"
        "Or just send a free-text question."
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


def _wait_for_job(job: Any, timeout_s: float) -> dict[str, Any]:
    """Block until RQ job finishes, fails, or times out."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job.refresh()
        status = str(job.get_status())
        if status.endswith("finished") or getattr(job, "is_finished", False):
            return {"ok": True, "result": job.return_value}
        if status.endswith("failed") or getattr(job, "is_failed", False):
            error = (job.exc_info or "job failed").strip().splitlines()[-1]
            return {"ok": False, "error": error}
        if status.endswith(("canceled", "cancelled", "stopped")):
            return {"ok": False, "error": f"job {status}"}
        time.sleep(0.5)
    return {"ok": False, "error": f"timed out after {int(timeout_s)}s"}


def _format_analyze(result: dict[str, Any]) -> str:
    interesting = result.get("interesting")
    lines = [
        f"Analyze {result.get('target')}:",
        f"  Period: {float(result.get('period_days', 0)):.3f} d",
        f"  SNR: {float(result.get('snr', 0)):.2f}",
        f"  Depth: ~{float(result.get('depth_ppm', 0)):.0f} ppm",
        f"  Flagged: {'yes' if interesting else 'no'}",
    ]
    if result.get("candidate_id") is not None:
        lines.append(f"  Candidate #{result['candidate_id']} (pending review)")
    reason = result.get("flag_reason")
    if reason:
        lines.append(f"  {reason}")
    return "\n".join(lines)


def _format_scan(result: dict[str, Any]) -> str:
    scanned = int(result.get("scanned", 0))
    flagged = int(result.get("flagged", 0))
    lines = [f"Scan complete: {scanned} analyzed, {flagged} flagged."]
    for row in result.get("results") or []:
        if not isinstance(row, dict):
            continue
        mark = "★" if row.get("interesting") else "·"
        lines.append(
            f"  {mark} {row.get('target')}: "
            f"P={float(row.get('period_days', 0)):.3f}d "
            f"SNR={float(row.get('snr', 0)):.1f}"
            + (f" → #{row['candidate_id']}" if row.get("candidate_id") is not None else "")
        )
    return "\n".join(lines)


def _format_ingest(result: dict[str, Any]) -> str:
    ingested = result.get("ingested") or []
    lines = [f"Ingest complete: {len(ingested)} targets."]
    for row in ingested:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"  • {row.get('target')}: {row.get('n_points')} pts "
            f"(source={row.get('source')})"
        )
    return "\n".join(lines)


def _format_summaries(result: dict[str, Any]) -> str:
    created = result.get("generated") or result.get("created") or []
    count = result.get("count", len(created) if isinstance(created, list) else 0)
    return f"Summaries generated: {count}."


def _format_notify(result: dict[str, Any]) -> str:
    if result.get("reason"):
        return f"Notify: {result.get('sent', 0)} sent ({result['reason']})."
    return f"Notify: digest sent to {result.get('sent', 0)} recipient(s)."


def _format_job_result(job_name: str, outcome: dict[str, Any]) -> str:
    if not outcome.get("ok"):
        return f"{job_name} failed: {outcome.get('error', 'unknown error')}"
    result = outcome.get("result")
    if not isinstance(result, dict):
        return f"{job_name} done: {result!r}"
    formatters: dict[str, Callable[[dict[str, Any]], str]] = {
        "analyze": _format_analyze,
        "scan": _format_scan,
        "ingest": _format_ingest,
        "summaries": _format_summaries,
        "notify": _format_notify,
    }
    formatter = formatters.get(job_name)
    if formatter is None:
        return f"{job_name} done: {result}"
    return formatter(result)


async def _enqueue_and_report(
    update: Update,
    job_name: str,
    func: Callable[..., Any],
    *args: Any,
    timeout_s: float = DEFAULT_JOB_TIMEOUT_S,
    **kwargs: Any,
) -> None:
    settings = get_settings()
    queue = get_queue(settings.tenant_id)
    job = queue.enqueue(func, *args, **kwargs)
    await update.message.reply_text(f"Queued {job_name} ({job.id}). Waiting for result…")

    outcome = await asyncio.to_thread(_wait_for_job, job, timeout_s)
    text = _format_job_result(job_name, outcome)
    # Telegram hard limit is 4096; keep replies compact.
    if len(text) > 3500:
        text = text[:3490] + "\n…"
    await update.message.reply_text(text)


async def ingest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await _enqueue_and_report(update, "ingest", exoplanet_ingest_job)


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await _enqueue_and_report(update, "scan", exoplanet_scan_job)


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    slug = " ".join(context.args).strip()
    if slug.lower().startswith("slug "):
        slug = slug[5:].strip()
    if not slug:
        await update.message.reply_text("Usage: /analyze <target-slug>\nExample: /analyze toi-715")
        return
    await _enqueue_and_report(update, "analyze", exoplanet_analyze_target_job, slug)


async def summaries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await _enqueue_and_report(update, "summaries", exoplanet_review_summary_job)


async def notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await _enqueue_and_report(update, "notify", exoplanet_notify_telegram_job)


def _parse_ask_args(args: list[str]) -> tuple[int | None, str]:
    """Parse `/ask [id] question...` → (candidate_id, question)."""
    if not args:
        return None, ""
    first = args[0].lstrip("#")
    if first.isdigit() and len(args) >= 2:
        return int(first), " ".join(args[1:]).strip()
    return None, " ".join(args).strip()


async def _reply_expert(update: Update, question: str, candidate_id: int | None) -> None:
    from projects.exoplanet.pipelines.expert import answer_exoplanet_question

    await update.message.reply_text("Thinking…")
    result = await asyncio.to_thread(
        answer_exoplanet_question,
        question,
        candidate_id=candidate_id,
    )
    if not result.get("ok"):
        reason = result.get("reason", "unknown")
        hint = result.get("hint")
        text = f"Expert unavailable ({reason})."
        if hint:
            text += f"\n{hint}"
        await update.message.reply_text(text)
        return
    answer = str(result.get("answer") or "")
    prefix = f"[candidate #{candidate_id} · {result.get('source')}]\n" if candidate_id else f"[{result.get('source')}]\n"
    text = prefix + answer
    if len(text) > 3500:
        text = text[:3490] + "\n…"
    await update.message.reply_text(text)


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    candidate_id, question = _parse_ask_args(list(context.args or []))
    if not question:
        await update.message.reply_text(
            "Usage:\n"
            "  /ask Is a 0.6 day period plausible for TOI-715?\n"
            "  /ask 8 Could this be an eclipsing binary?"
        )
        return
    await _reply_expert(update, question, candidate_id)


async def free_text_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Authorized free-text messages go to the exoplanet expert."""
    settings = get_settings()
    if not _authorized(update, settings.allowed_user_ids):
        await _deny(update)
        return
    text = (update.message.text or "").strip() if update.message else ""
    if not text or text.startswith("/"):
        return
    await _reply_expert(update, text, None)


async def echo_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if _authorized(update, settings.allowed_user_ids):
        return
    await _deny(update)


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
    app.add_handler(CommandHandler("ask", ask))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text_ask))
    app.add_handler(MessageHandler(filters.ALL, echo_unauthorized))

    logger.info("exoplanet_bot_starting")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
