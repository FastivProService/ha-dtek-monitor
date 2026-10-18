"""Opt-in local HA reminders. No billing writes and no Telegram dependency."""

import asyncio
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import logging

from homeassistant.components import persistent_notification as pn
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .account import CONF_ACCOUNTS, AccountError, account_key
from .account_submission import SubmissionError
from .providers import CONF_PROVIDER
from .schedule import KYIV_TZ

CONF_REMINDERS = "account_reminders"
DEFAULT_TIME = "19:00:00"
_LOGGER = logging.getLogger(__name__)


def reading_window(day: date):
    """OEM's last two / first three days, including year and leap transitions."""
    last = monthrange(day.year, day.month)[1]
    if day.day <= 3:
        month_end = day.replace(day=1) - timedelta(days=1)
    elif day.day >= last - 1:
        month_end = day.replace(day=last)
    else:
        return None
    return (
        month_end.strftime("%Y-%m"),
        month_end - timedelta(days=1),
        month_end + timedelta(days=3),
    )


def settings(entry, number):
    return {
        "enabled": False,
        "time": DEFAULT_TIME,
        "include_estimate": True,
        **entry.options.get(CONF_REMINDERS, {}).get(number, {}),
    }


class AccountReminders:
    """One dismissible reminder per reporting window, restored after HA restart."""

    def __init__(self, hass, entry, coordinator):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.store = Store(hass, 1, f"dtek_monitor.reminders.{entry.entry_id}")
        self.records = {}
        self._shown = set()
        self._lock = asyncio.Lock()
        self._closed = False
        self._unsub = []
        self._tasks = set()

    def key(self, number):
        return account_key(self.entry.data[CONF_PROVIDER], number)

    def notification_id(self, key):
        return f"dtek_reading_{self.entry.entry_id}_{key}"

    async def start(self):
        try:
            saved = await self.store.async_load()
            if saved is not None:
                if not isinstance(saved, dict) or any(
                    not isinstance(row, dict)
                    or not isinstance(row.get("period"), str)
                    or not isinstance(row.get("dismissed"), bool)
                    for row in saved.values()
                ):
                    raise ValueError("Invalid reminder state")
                self.records = saved
        except (OSError, ValueError, TypeError):
            # Losing the duplicate/dismissal ledger must not start a new campaign.
            _LOGGER.error("Cannot restore DTEK reading reminders; reminders disabled")
            self._closed = True
            return
        self._unsub = [
            pn.async_register_callback(self.hass, self._notification_changed),
            async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)),
        ]
        await self._tick()

    async def close(self):
        self._closed = True
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        if self._tasks:
            await asyncio.gather(*self._tasks)

    @callback
    def _notification_changed(self, update_type, notifications):
        if update_type != pn.UpdateType.REMOVED or self._closed:
            return
        for key in list(self._shown):
            if self.notification_id(key) in notifications:
                self._shown.discard(key)
                record = self.records[key]
                record["dismissed"] = True
                task = self.hass.async_create_task(
                    self._acknowledge(key, record["period"])
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _acknowledge(self, key, period):
        async with self._lock:
            if self.records.get(key, {}).get("period") != period:
                return
            try:
                await self.store.async_save(self.records)
            except OSError:
                _LOGGER.error("Cannot save DTEK reminder dismissal")

    def _dismiss(self, key):
        self._shown.discard(key)
        pn.async_dismiss(self.hass, self.notification_id(key))

    async def _tick(self, now=None):
        async with self._lock:
            if self._closed:
                return
            now = (now or datetime.now(KYIV_TZ)).astimezone(KYIV_TZ)
            window = reading_window(now.date())
            selected = {self.key(n): n for n in self.entry.data[CONF_ACCOUNTS]}
            for key in self.records.keys() - selected.keys():
                self._dismiss(key)
            for key, number in selected.items():
                config = settings(self.entry, number)
                if not config["enabled"] or window is None:
                    self._dismiss(key)
                    continue
                period = window[0]
                record = self.records.get(key, {})
                if record.get("period") != period and now.time() < time.fromisoformat(
                    config["time"]
                ):
                    continue
                if record.get("period") == period and (
                    record.get("dismissed") or key in self._shown
                ):
                    continue
                # Build only when due, never poll the cabinet every timer tick.
                title, message = await self.message(number, now)
                if self._closed:
                    return
                self.records[key] = {"period": period, "dismissed": False}
                try:
                    await self.store.async_save(self.records)
                except OSError:
                    _LOGGER.error("Cannot save DTEK reminder; delivery postponed")
                    continue
                if self._closed:
                    return
                pn.async_create(self.hass, message, title, self.notification_id(key))
                self._shown.add(key)

    async def preview(self, number):
        """An explicit local preview never consumes or enables scheduled reminders."""
        title, message = await self.message(number, datetime.now(KYIV_TZ), preview=True)
        if not self._closed:
            pn.async_create(
                self.hass,
                message,
                title,
                self.notification_id(self.key(number)) + "_preview",
            )

    async def message(self, number, now, preview=False):
        uk = self.hass.config.language == "uk"
        title = ("ДТЕК · покази" if uk else "DTEK · readings") + f" · ••••{number[-4:]}"
        lines = [
            (
                "Перевірте покази лічильника та передайте їх у ДТЕК."
                if uk
                else "Check your meter readings and submit them to DTEK."
            ),
        ]
        if preview:
            lines.insert(
                0, "**Перевірка нагадування**" if uk else "**Reminder preview**"
            )
        window = reading_window(now.date())
        if window:
            lines.append(
                (
                    f"Період: **{window[0]}**. Передача показів: {window[1]:%d.%m}–{window[2]:%d.%m}."
                    if uk
                    else f"Period: **{window[0]}**. Reading window: {window[1]:%d.%m}–{window[2]:%d.%m}."
                )
            )
        if settings(self.entry, number)["include_estimate"]:
            try:
                view = await self.coordinator.energy.summary(number)
                if view.get("ready"):
                    context = await self.coordinator.client.read_submission_context(
                        number
                    )
                    binding = view["binding"]
                    if (
                        binding["serial"] != context["serial"]
                        or set(binding["zones"])
                        != {z["zone"] for z in context["zones"]}
                        or any(
                            Decimal(view["readings"][z["zone"]]["send_value"])
                            < Decimal(z["previous"])
                            for z in context["zones"]
                        )
                    ):
                        raise SubmissionError("energy_changed")
                    labels = {
                        "GENERAL": "Однозонний" if uk else "Single zone",
                        "DAY": "День" if uk else "Day",
                        "NIGHT": "Ніч" if uk else "Night",
                    }
                    lines.append(
                        "**Розрахунок за датчиком:**" if uk else "**Sensor estimate:**"
                    )
                    lines.extend(
                        f"- {labels.get(zone, zone)}: **{row['send_value']} {'кВт·год' if uk else 'kWh'}**"
                        for zone, row in view["readings"].items()
                    )
                    lines.append(
                        "Звірте розрахунок із фізичним лічильником перед передачею."
                        if uk
                        else "Compare the estimate with the physical meter before submitting."
                    )
                elif view.get("binding"):
                    raise SubmissionError("source_unavailable")
            except (AccountError, SubmissionError):
                lines.append(
                    "Розрахунок за датчиком зараз недоступний або не підтверджений. Зніміть покази вручну."
                    if uk
                    else "The sensor estimate is unavailable or unverified. Read the meter manually."
                )
        lines.extend(
            [
                (
                    "Якщо вже передали покази, закрийте це нагадування."
                    if uk
                    else "If you have already submitted readings, dismiss this reminder."
                ),
                (
                    "Відкрийте картку передачі показів на своїй панелі HA. Нічого не передано автоматично."
                    if uk
                    else "Open the readings card on your HA dashboard. Nothing has been submitted automatically."
                ),
                now.strftime("%d.%m.%Y %H:%M") + " · Europe/Kyiv",
            ]
        )
        return title, "\n\n".join(lines)
