"""Fixed signals/telegram_listener.py — reconnect logic, error resilience"""
"""Real-time Telegram listener for @CKPscanner.
Adds: auto-reconnect on disconnect, error resilience per-message.
"""

import asyncio
import time
from telethon import TelegramClient, events
from telethon.errors import ConnectionError, TimeoutError as TelethonTimeout
from loguru import logger
from config.settings import settings
from src.signals.parser import parse_ckp_message, SignalCandidate


class TelegramListener:
    def __init__(self):
        self.session_path = f"{settings.DATA_DIR}/telegram_session/{settings.TELEGRAM_SESSION}"
        self.client = TelegramClient(
            self.session_path,
            settings.TELEGRAM_API_ID,
            settings.TELEGRAM_API_HASH,
        )
        self.channel = settings.TELEGRAM_CHANNEL
        self.on_signal_callback = None
        self._reconnect_delay = 5
        self._max_reconnect_delay = 300

    def set_signal_callback(self, callback):
        """Set a coroutine that will be called with List[SignalCandidate]."""
        self.on_signal_callback = callback

    async def start(self):
        logger.info(f"Starting Telegram listener for {self.channel}...")
        await self._run_with_reconnect()

    async def _run_with_reconnect(self):
        """Start client with automatic reconnect on failure."""
        while True:
            try:
                await self.client.start()
                me = await self.client.get_me()
                logger.success(f"Telegram logged in as: {me.first_name} ({me.id})")

                @self.client.on(events.NewMessage(chats=self.channel))
                async def handler(event):
                    try:
                        text = event.message.message
                        if not text:
                            return

                        logger.debug(f"Message preview: {text[:120]}...")

                        candidates = parse_ckp_message(text)
                        if candidates:
                            logger.info(f"New signal batch: {len(candidates)} candidates")
                            for c in candidates:
                                logger.info(
                                    f"  → {c.side.upper():5} | {c.symbol:8} | "
                                    f"rank={c.rank} | conf={c.confidence:.1f} | {c.raw_line[:60]}"
                                )
                            if self.on_signal_callback:
                                await self.on_signal_callback(candidates)
                        else:
                            logger.debug("No tradeable candidates in this message")
                    except Exception as e:
                        logger.exception(f"Error handling message {event.message.id}: {e}")

                logger.success(f"Listening to {self.channel} ... (Ctrl+C to stop)")
                await self.client.run_until_disconnected()

            except (ConnectionError, TelethonTimeout, Exception) as e:
                logger.warning(f"Telegram connection lost: {e}. Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    async def stop(self):
        await self.client.disconnect()
        logger.info("Telegram client disconnected")
