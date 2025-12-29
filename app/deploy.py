# -*- coding: utf-8 -*-
"""
Epic Games Free Game Collection Deployment Module

This module orchestrates the automated collection of free games from Epic Games Store
using browser automation and scheduling capabilities.

@Time    : 2025/7/16 21:28
@Author  : QIN2DIM
@GitHub  : https://github.com/QIN2DIM
"""

import asyncio
import json
import signal
import sys
from contextlib import suppress
from datetime import datetime
from time import monotonic

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from browserforge.fingerprints import Screen
from camoufox import AsyncCamoufox
from loguru import logger
from playwright.async_api import ViewportSize
from pytz import timezone

from notifications.telegram import TelegramConfig, send_telegram_message
from services.epic_authorization_service import EpicAuthorization
from services.epic_games_service import CollectionResult, EpicAgent
from settings import LOG_DIR, RECORD_DIR
from settings import settings
from utils import init_log

# Initialize logging configuration for runtime, error, and serialization logs
init_log(
    runtime=LOG_DIR.joinpath("runtime.log"),
    error=LOG_DIR.joinpath("error.log"),
    serialize=LOG_DIR.joinpath("serialize.log"),
)

# Default timezone for scheduling operations
TIMEZONE = timezone("Asia/Shanghai")


def _mask_email(email: str) -> str:
    email = (email or "").strip()
    if "@" not in email:
        return email or "<unknown>"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[:1] + "***"
    else:
        masked_local = local[:1] + "***" + local[-1:]
    return f"{masked_local}@{domain}"


def _format_promotions(result: CollectionResult) -> str:
    if not result.promotions:
        return "周免: 0"
    lines = [f"周免: {len(result.promotions)}"]
    for p in result.promotions[:10]:
        lines.append(f"- {p.title}")
    if len(result.promotions) > 10:
        lines.append(f"- ... (+{len(result.promotions) - 10})")
    return "\n".join(lines)


async def _maybe_notify(text: str, *, success: bool) -> None:
    if success and not settings.TG_NOTIFY_ON_SUCCESS:
        return
    if not success and not settings.TG_NOTIFY_ON_FAILURE:
        return

    if not settings.TG_BOT_TOKEN or not settings.TG_CHAT_ID:
        return

    token = settings.TG_BOT_TOKEN.get_secret_value().strip()
    chat_id = str(settings.TG_CHAT_ID).strip()
    if not token or not chat_id:
        return

    config = TelegramConfig(
        bot_token=token,
        chat_id=chat_id,
        disable_web_page_preview=settings.TG_DISABLE_WEB_PAGE_PREVIEW,
    )
    await send_telegram_message(config, text)


@logger.catch(reraise=True)
async def execute_browser_tasks(headless: bool = True) -> CollectionResult:
    """
    Execute Epic Games free game collection tasks using browser automation.

    This function handles the complete workflow of authenticating with Epic Games
    and collecting available free games through browser automation.

    Args:
        headless: Whether to run browser in headless mode
    """
    logger.debug("Starting Epic Games collection task")

    # Configure browser with anti-detection features and video recording
    async with AsyncCamoufox(
        persistent_context=True,
        user_data_dir=settings.user_data_dir,
        screen=Screen(max_width=1920, max_height=1080, min_height=1080, min_width=1920),
        record_video_dir=RECORD_DIR,
        record_video_size=ViewportSize(width=1920, height=1080),
        humanize=0.2,
        headless=headless,
    ) as browser:
        # Initialize or reuse existing browser page
        page = browser.pages[0] if browser.pages else await browser.new_page()
        logger.debug("Browser initialized successfully")

        # Handle Epic Games authentication
        logger.debug("Initiating Epic Games authentication")
        agent = EpicAuthorization(page)
        await agent.invoke()
        logger.debug("Authentication completed")

        # Execute a free games collection on new page
        logger.debug("Starting free games collection process")
        game_page = await browser.new_page()
        agent = EpicAgent(game_page)
        result = await agent.collect_epic_games()
        logger.debug("Free games collection completed")

        # Cleanup browser resources
        logger.debug("Cleaning up browser resources")
        with suppress(Exception):
            for p in browser.pages:
                await p.close()

        with suppress(Exception):
            await browser.close()

        logger.debug("Browser tasks execution finished successfully")
        return result


async def run_collection_task(headless: bool, trigger: str):
    started = monotonic()
    masked_email = _mask_email(settings.EPIC_EMAIL)
    now = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %Z")

    try:
        result = await execute_browser_tasks(headless=headless)
        elapsed_s = monotonic() - started

        if result.outcome == "already_in_library":
            text = (
                f"[Epic Awesome Gamer] 任务完成（{trigger}）\n"
                f"账号: {masked_email}\n"
                f"结果: 已在库/无可领取\n"
                f"耗时: {elapsed_s:.1f}s\n"
                f"时间: {now}"
            )
            await _maybe_notify(text, success=True)
            return

        if result.outcome == "completed":
            text = (
                f"[Epic Awesome Gamer] 任务完成（{trigger}）\n"
                f"账号: {masked_email}\n"
                f"{_format_promotions(result)}\n"
                f"结果: 流程完成（可能已领取/已在库）\n"
                f"耗时: {elapsed_s:.1f}s\n"
                f"时间: {now}"
            )
            await _maybe_notify(text, success=True)
            return

        if result.outcome == "not_logged_in":
            text = (
                f"[Epic Awesome Gamer] 任务失败（{trigger}）\n"
                f"账号: {masked_email}\n"
                f"结果: 登录状态无效/未登录\n"
                f"耗时: {elapsed_s:.1f}s\n"
                f"时间: {now}"
            )
            await _maybe_notify(text, success=False)
            return

        text = (
            f"[Epic Awesome Gamer] 任务失败（{trigger}）\n"
            f"账号: {masked_email}\n"
            f"{_format_promotions(result)}\n"
            f"错误: {result.error or 'unknown error'}\n"
            f"耗时: {elapsed_s:.1f}s\n"
            f"时间: {now}"
        )
        await _maybe_notify(text, success=False)
    except Exception as err:
        elapsed_s = monotonic() - started
        text = (
            f"[Epic Awesome Gamer] 任务失败（{trigger}）\n"
            f"账号: {masked_email}\n"
            f"错误: {err}\n"
            f"耗时: {elapsed_s:.1f}s\n"
            f"时间: {now}"
        )
        await _maybe_notify(text, success=False)
        logger.exception(err)
        return


async def deploy():
    """
    Main deployment function that executes Epic Games collection tasks.

    This function runs the collection process immediately and optionally
    sets up a scheduled task for automatic recurring execution.
    """
    headless = True

    # Log current configuration for debugging
    sj = settings.model_dump(mode="json")
    sj["headless"] = headless
    logger.debug(
        f"Starting deployment with configuration: {json.dumps(sj, indent=2, ensure_ascii=False)}"
    )

    # Execute an immediate collection task
    await run_collection_task(headless=headless, trigger="startup")

    # Skip scheduler setup if disabled in configuration
    if not settings.ENABLE_APSCHEDULER:
        logger.debug("Scheduler is disabled, deployment completed")
        return

    # Initialize and configure async scheduler
    scheduler = AsyncIOScheduler()

    # Strategy 1: Thursday 23:30 to Friday 03:30, every hour (Beijing Time)
    scheduler.add_job(
        run_collection_task,
        trigger=CronTrigger(
            day_of_week="thu", hour="23,0,1,2,3", minute="30", timezone="Asia/Shanghai"
        ),
        id="weekly_epic_games_task",
        name="weekly_epic_games_task",
        args=[headless, "weekly"],
        replace_existing=False,
        max_instances=1,
    )

    # Strategy 2: Daily at 12:00 PM (Beijing Time)
    scheduler.add_job(
        run_collection_task,
        trigger=CronTrigger(hour="12", minute="0", timezone="Asia/Shanghai"),
        id="daily_epic_games_task",
        name="daily_epic_games_task",
        args=[headless, "daily"],
        replace_existing=False,
        max_instances=1,
    )

    # Set up graceful shutdown signal handlers
    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.debug(f"Received signal {signal.Signals(signum).name}, initiating graceful shutdown")
        shutdown_event.set()

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start scheduler and log status information
    scheduler.start()
    logger.debug("Epic Games scheduler started successfully")
    logger.debug(f"Current time: {datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # Log next execution times for all scheduled jobs
    for j in scheduler.get_jobs():
        if next_run := j.next_run_time:
            logger.debug(
                f"Next execution scheduled: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')} (job_id: {j.id})"
            )

    # Keep scheduler running until shutdown signal received
    logger.debug("Scheduler is running, send SIGINT or SIGTERM to stop gracefully")
    try:
        await shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown(wait=True)
        logger.success("Scheduler stopped gracefully")


if __name__ == '__main__':
    asyncio.run(deploy())
