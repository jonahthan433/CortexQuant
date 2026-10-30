"""Sequential Telegram ingestion backed by the Postgres inbox with bounded backpressure."""
import asyncio, os
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
from telethon import TelegramClient, events
from config.settings import settings
from src.signals.translator import translate

class TelegramListener:
    def __init__(self, ledger):
        directory = Path(settings.DATA_DIR) / 'telegram_session'
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        self.session_path = str(directory / settings.TELEGRAM_SESSION)
        if Path(self.session_path + '.session').exists():
            os.chmod(self.session_path + '.session', 0o600)
        self.client = TelegramClient(self.session_path, settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH)
        self.channel = settings.TELEGRAM_CHANNEL
        self.ledger = ledger
        self.callback = None
        self._lock = asyncio.Lock()
        self.connected = False

        # Bounded async queue for backpressure handling
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._worker_task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()

        self.client.add_event_handler(self._handle_message, events.NewMessage(chats=self.channel))

    def set_signal_callback(self, callback):
        self.callback = callback

    async def alert(self, message):
        try:
            if self.client.is_connected():
                await self.client.send_message('me', message)
                return True
            logger.warning('telegram_alert_deferred reason=client_disconnected')
        except Exception as e:
            logger.warning('telegram_alert_failed error={}', type(e).__name__)
        return False

    async def _worker(self):
        """Background worker that processes messages with controlled concurrency."""
        while not self._shutdown.is_set():
            try:
                # Wait for message with timeout to allow shutdown check
                ch, mid, text, received = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
                try:
                    intents = translate(text, f'{ch}:{mid}', received)
                    logger.info('telegram_message_translated channel_id={} message_id={} intents={}', ch, mid, len(intents))
                    if intents and self.callback:
                        _r = self.callback(intents)
                        if asyncio.iscoroutine(_r):
                            await _r
                    self.ledger.mark_processed(ch, mid)
                except Exception as e:
                    logger.exception('telegram_inbox_processing_failed channel_id={} message_id={} error={}', ch, mid, type(e).__name__)
                finally:
                    self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception('telegram_worker_error error={}', type(e).__name__)

    async def drain(self):
        """Drain pending messages from Postgres into the async queue."""
        async with self._lock:
            for row in self.ledger.pending():
                ch, mid, text, received = row['channel_id'], row['message_id'], row['raw_text'], row['received_at']
                try:
                    await asyncio.wait_for(self._queue.put((ch, mid, text, received)), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning('telegram_queue_full_dropped channel_id={} message_id={}', ch, mid)
                    # Message stays in Postgres pending state for next drain attempt

    async def _handle_message(self, event):
        msg = event.message
        body = msg.message or ''
        if not body:
            return
        ch = int(event.chat_id or 0)
        mid = int(msg.id)
        stamp = datetime.now(timezone.utc)
        try:
            inserted = self.ledger.enqueue(ch, mid, body, stamp)
            if not inserted:
                logger.info('telegram_duplicate_ignored channel_id={} message_id={}', ch, mid)
                return
            # Put directly in queue (non-blocking, drop if full)
            try:
                self._queue.put_nowait((ch, mid, body, stamp))
            except asyncio.QueueFull:
                logger.warning('telegram_queue_full_incoming_dropped channel_id={} message_id={}', ch, mid)
        except Exception as e:
            logger.exception('telegram_ingest_failed error={}', type(e).__name__)

    async def start(self):
        previous_umask = os.umask(0o077)
        try:
            await self.client.start()
        finally:
            os.umask(previous_umask)
        f = Path(self.session_path + '.session')
        if f.exists():
            os.chmod(f, 0o600)
        self.connected = True
        me = await self.client.get_me()
        logger.info('telegram_connected account_id={}', me.id)

        # Start background worker
        self._shutdown.clear()
        self._worker_task = asyncio.create_task(self._worker())

        await self.drain()
        try:
            await self.client.run_until_disconnected()
        finally:
            self.connected = False

    async def stop(self):
        self._shutdown.set()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self.connected = False
        await self.client.disconnect()
