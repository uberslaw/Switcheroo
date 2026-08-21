from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings
from app.db import SessionLocal
from app.diagnostics import sync_log_level
from app.models import Switch, utcnow
from app.services.request_service import sync_servicenow_tickets
from app.services.switch_service import monitored_switches, poll_switch_daily, poll_switch_status, tick_troubleshooting

log = logging.getLogger("switcheroo.poller")

_scheduler: BackgroundScheduler | None = None


def poll_all_status() -> None:
    sync_log_level()
    db = SessionLocal()
    try:
        switches = monitored_switches(db)
        for switch in switches:
            try:
                poll_switch_status(db, switch)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("Unhandled status poll error for %s", switch.name)
                try:
                    switch.last_poll_error = "Unhandled poller exception (see switcheroo.log)"
                    switch.next_status_poll_at = utcnow() + timedelta(seconds=get_settings().status_poll_interval)
                    db.add(switch)
                    db.commit()
                except Exception:
                    db.rollback()
    finally:
        db.close()


def poll_all_daily() -> None:
    db = SessionLocal()
    try:
        switches = monitored_switches(db)
        for switch in switches:
            try:
                poll_switch_daily(db, switch)
                db.commit()
            except Exception:
                db.rollback()
                log.exception("Unhandled daily poll error for %s", switch.name)
    finally:
        db.close()


def tick_all_troubleshooting() -> None:
    db = SessionLocal()
    try:
        tick_troubleshooting(db)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Troubleshooting tick failed")
    finally:
        db.close()


def poll_servicenow() -> None:
    db = SessionLocal()
    try:
        updated = sync_servicenow_tickets(db)
        db.commit()
        if updated:
            log.info("ServiceNow poll linked %s local request(s)", updated)
    except Exception:
        db.rollback()
        log.exception("ServiceNow ticket poll failed")
    finally:
        db.close()


def start_poller() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        poll_all_status,
        "interval",
        seconds=settings.status_poll_interval,
        id="status-poll",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        poll_all_daily,
        "cron",
        hour=settings.daily_poll_hour,
        minute=0,
        id="daily-poll",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        tick_all_troubleshooting,
        "interval",
        seconds=min(settings.troubleshoot_interval, 10),
        id="troubleshoot-tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        poll_servicenow,
        "interval",
        seconds=settings.servicenow_poll_seconds,
        id="servicenow-poll",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    log.info(
        "Poller started: status every %ss (targeted ifOperStatus), daily details at %02d:00 UTC, "
        "troubleshoot tick every %ss, ServiceNow every %ss (live=%s dry_run=%s)",
        settings.status_poll_interval,
        settings.daily_poll_hour,
        min(settings.troubleshoot_interval, 10),
        settings.servicenow_poll_seconds,
        settings.servicenow_live,
        settings.servicenow_dry_run,
    )
    return scheduler


def stop_poller() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Poller stopped")
