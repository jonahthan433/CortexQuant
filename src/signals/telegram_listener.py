"""Sequential Telegram ingestion backed by the Postgres inbox."""
import asyncio, os
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
from telethon import TelegramClient, events
from config.settings import settings
from src.signals.translator import translate

class TelegramListener:
    def __init__(self, ledger):
        directory=Path(settings.DATA_DIR)/'telegram_session'; directory.mkdir(parents=True,exist_ok=True); os.chmod(directory,0o700)
        self.session_path=str(directory/settings.TELEGRAM_SESSION)
        if Path(self.session_path+'.session').exists(): os.chmod(self.session_path+'.session',0o600)
        self.client=TelegramClient(self.session_path,settings.TELEGRAM_API_ID,settings.TELEGRAM_API_HASH)
        self.channel=settings.TELEGRAM_CHANNEL; self.ledger=ledger; self.callback=None; self._lock=asyncio.Lock()
        self.connected=False
        self.client.add_event_handler(self._handle_message,events.NewMessage(chats=self.channel))
    def set_signal_callback(self,callback): self.callback=callback
    async def alert(self,message):
        try:
            if self.client.is_connected():
                await self.client.send_message('me',message)
                return True
            logger.warning('telegram_alert_deferred reason=client_disconnected')
        except Exception as e: logger.warning('telegram_alert_failed error={}',type(e).__name__)
        return False
    async def drain(self):
        async with self._lock:
            for row in self.ledger.pending():
                ch,mid,text,received=row['channel_id'],row['message_id'],row['raw_text'],row['received_at']
                try:
                    intents=translate(text,f'{ch}:{mid}',received)
                    logger.info('telegram_message_translated channel_id={} message_id={} intents={}',ch,mid,len(intents))
                    if intents and self.callback: await self.callback(intents)
                    self.ledger.mark_processed(ch,mid)
                except Exception as e:
                    logger.exception('telegram_inbox_processing_failed channel_id={} message_id={} error={}',ch,mid,type(e).__name__); return
    async def _handle_message(self,event):
        msg=event.message; body=msg.message or ''
        if not body: return
        ch=int(event.chat_id or 0); mid=int(msg.id); stamp=datetime.now(timezone.utc)
        try:
            inserted=self.ledger.enqueue(ch,mid,body,stamp)
            if not inserted: logger.info('telegram_duplicate_ignored channel_id={} message_id={}',ch,mid)
            await self.drain()
        except Exception as e: logger.exception('telegram_ingest_failed error={}',type(e).__name__)
    async def start(self):
        previous_umask=os.umask(0o077)
        try: await self.client.start()
        finally: os.umask(previous_umask)
        f=Path(self.session_path+'.session')
        if f.exists(): os.chmod(f,0o600)
        self.connected=True
        me=await self.client.get_me(); logger.info('telegram_connected account_id={}',me.id)
        await self.drain()
        await self.client.run_until_disconnected()
        self.connected=False
    async def stop(self):
        self.connected=False
        await self.client.disconnect()
