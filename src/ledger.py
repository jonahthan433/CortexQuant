"""Postgres-backed durable trading ledger and Telegram inbox."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
import psycopg
from psycopg.rows import dict_row
from config.settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS system_state (key text primary key, value jsonb not null, updated_at timestamptz not null default now());
CREATE TABLE IF NOT EXISTS telegram_inbox (sequence bigserial primary key, channel_id bigint not null, message_id bigint not null, raw_text text not null, received_at timestamptz not null, state text not null default 'pending', unique(channel_id,message_id));
CREATE INDEX IF NOT EXISTS telegram_inbox_pending ON telegram_inbox(sequence) WHERE state='pending';
CREATE TABLE IF NOT EXISTS orders (client_order_id text primary key, symbol text not null, side text not null, order_type text not null, quantity numeric not null, price numeric, status text not null, reduce_only boolean not null default false, metadata jsonb not null default '{}', created_at timestamptz not null default now());
CREATE TABLE IF NOT EXISTS fills (fill_id bigserial primary key, client_order_id text not null references orders(client_order_id), symbol text not null, side text not null, quantity numeric not null, price numeric not null, fee numeric not null default 0, realized_pnl numeric not null default 0, filled_at timestamptz not null default now());
CREATE TABLE IF NOT EXISTS positions (symbol text primary key, side text not null, quantity numeric not null, entry_price numeric not null, stop_price numeric, target_price numeric, updated_at timestamptz not null default now());
CREATE TABLE IF NOT EXISTS daily_pnl (trade_date date primary key, starting_equity numeric not null, realized_pnl numeric not null default 0, updated_at timestamptz not null default now());
CREATE TABLE IF NOT EXISTS execution_events (event_id bigserial primary key, event_type text not null, payload jsonb not null, created_at timestamptz not null default now());
"""

class PortfolioLedger:
    def __init__(self, dsn: str | None = None): self.dsn = dsn or settings.DATABASE_URL
    @contextmanager
    def connect(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5, client_encoding='UTF8') as conn:
            yield conn
    def initialize(self):
        with self.connect() as c: c.execute(SCHEMA)
    def health(self):
        with self.connect() as c: c.execute("SELECT 1").fetchone()
        return True
    def get_state(self, key, default=None):
        with self.connect() as c:
            r=c.execute("SELECT value FROM system_state WHERE key=%s",(key,)).fetchone()
            return r["value"] if r else default
    def set_state(self, key, value):
        with self.connect() as c: c.execute("INSERT INTO system_state(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=now()",(key,json.dumps(value, default=str)))
    def paper_start(self, configured=None):
        with self.connect() as c:
            r=c.execute("SELECT value FROM system_state WHERE key='paper_start_ts' FOR UPDATE").fetchone()
            if r: return datetime.fromisoformat(r['value']) if isinstance(r['value'], str) else datetime.fromisoformat(r['value']['ts'])
            stamp=(configured or datetime.now(timezone.utc)).astimezone(timezone.utc)
            c.execute("INSERT INTO system_state(key,value) VALUES('paper_start_ts',%s)",(json.dumps(stamp.isoformat()),))
            return stamp
    def enqueue(self, channel_id, message_id, raw_text, received_at):
        with self.connect() as c:
            r=c.execute("INSERT INTO telegram_inbox(channel_id,message_id,raw_text,received_at) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING sequence",(channel_id,message_id,raw_text,received_at)).fetchone()
            return bool(r)
    def pending(self):
        with self.connect() as c: return c.execute("SELECT channel_id,message_id,raw_text,received_at FROM telegram_inbox WHERE state='pending' ORDER BY sequence").fetchall()
    def mark_processed(self, ch, mid):
        with self.connect() as c: c.execute("UPDATE telegram_inbox SET state='processed' WHERE channel_id=%s AND message_id=%s",(ch,mid))
    def positions(self):
        with self.connect() as c: return c.execute("SELECT * FROM positions ORDER BY symbol").fetchall()
    def kill_switch(self): return bool(self.get_state('kill_switch', {'enabled':False}).get('enabled',False))
    def set_kill_switch(self, enabled, reason='operator'):
        self.set_state('kill_switch', {'enabled':bool(enabled),'reason':reason,'updated_at':datetime.now(timezone.utc).isoformat()})
    def event(self, event_type, payload):
        with self.connect() as c: c.execute("INSERT INTO execution_events(event_type,payload) VALUES(%s,%s)",(event_type,json.dumps(payload,default=str,ensure_ascii=True)))
    def day_pnl(self, day=None):
        with self.connect() as c:
            r=c.execute("SELECT realized_pnl,starting_equity FROM daily_pnl WHERE trade_date=%s",(day or datetime.now(timezone.utc).date(),)).fetchone()
            return (float(r['realized_pnl']),float(r['starting_equity'])) if r else (0.0,0.0)
    def initialize_day(self, equity, day=None):
        with self.connect() as c: c.execute("INSERT INTO daily_pnl(trade_date,starting_equity) VALUES(%s,%s) ON CONFLICT(trade_date) DO NOTHING",(day or datetime.now(timezone.utc).date(),equity))
    def record_paper_entry_bundle(self, client_id, symbol, side, qty, price, stop, target, fee, source_id, stop_id, target_id):
        close_side='sell' if side=='long' else 'buy'
        with self.connect() as c:
            if c.execute("SELECT 1 FROM positions WHERE symbol=%s FOR UPDATE",(symbol,)).fetchone(): return False
            inserted=c.execute("INSERT INTO orders(client_order_id,symbol,side,order_type,quantity,price,status,reduce_only,metadata) VALUES(%s,%s,%s,'market',%s,%s,'filled',false,%s) ON CONFLICT DO NOTHING RETURNING client_order_id",(client_id,symbol,side,qty,price,json.dumps({'paper':True,'source_id':source_id}))).fetchone()
            if not inserted: return False
            c.execute("INSERT INTO fills(client_order_id,symbol,side,quantity,price,fee) VALUES(%s,%s,%s,%s,%s,%s)",(client_id,symbol,'buy' if side=='long' else 'sell',qty,price,fee))
            c.execute("INSERT INTO daily_pnl(trade_date,starting_equity,realized_pnl) VALUES(%s,0,%s) ON CONFLICT(trade_date) DO UPDATE SET realized_pnl=daily_pnl.realized_pnl+excluded.realized_pnl,updated_at=now()",(datetime.now(timezone.utc).date(),-fee))
            c.execute("INSERT INTO positions(symbol,side,quantity,entry_price,stop_price,target_price) VALUES(%s,%s,%s,%s,%s,%s)",(symbol,side,qty,price,stop,target))
            for pid,kind,level in ((stop_id,'stop_loss',stop),(target_id,'take_profit',target)):
                c.execute("INSERT INTO orders(client_order_id,symbol,side,order_type,quantity,price,status,reduce_only,metadata) VALUES(%s,%s,%s,%s,%s,%s,'open',true,'{\"paper\":true}'::jsonb) ON CONFLICT DO NOTHING",(pid,symbol,close_side,kind,qty,level))
            c.execute("INSERT INTO execution_events(event_type,payload) VALUES('paper_entry_filled',%s)",(json.dumps({'client_order_id':client_id,'symbol':symbol,'side':side,'quantity':qty,'price':price,'stop':stop,'target':target,'fee':fee}),))
            return True
    def record_order(self, client_id,symbol,side,kind,qty,price,status='filled',reduce_only=False,metadata=None):
        with self.connect() as c: c.execute("INSERT INTO orders(client_order_id,symbol,side,order_type,quantity,price,status,reduce_only,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(client_order_id) DO NOTHING",(client_id,symbol,side,kind,qty,price,status,reduce_only,json.dumps(metadata or {},ensure_ascii=True)))
    def record_fill(self, client_id,symbol,side,qty,price,fee=0,realized=0):
        with self.connect() as c:
            c.execute("INSERT INTO fills(client_order_id,symbol,side,quantity,price,fee,realized_pnl) VALUES(%s,%s,%s,%s,%s,%s,%s)",(client_id,symbol,side,qty,price,fee,realized))
            delta=realized-fee
            c.execute("INSERT INTO daily_pnl(trade_date,starting_equity,realized_pnl) VALUES(%s,0,%s) ON CONFLICT(trade_date) DO UPDATE SET realized_pnl=daily_pnl.realized_pnl+excluded.realized_pnl,updated_at=now()",(datetime.now(timezone.utc).date(),delta))
    def upsert_position(self,symbol,side,qty,entry,stop=None,target=None):
        with self.connect() as c: c.execute("INSERT INTO positions(symbol,side,quantity,entry_price,stop_price,target_price) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(symbol) DO UPDATE SET side=excluded.side,quantity=excluded.quantity,entry_price=excluded.entry_price,stop_price=excluded.stop_price,target_price=excluded.target_price,updated_at=now()",(symbol,side,qty,entry,stop,target))
    def remove_position(self,symbol):
        with self.connect() as c: c.execute("DELETE FROM positions WHERE symbol=%s",(symbol,))
    def close_protection_orders(self,symbol):
        with self.connect() as c: c.execute("UPDATE orders SET status='cancelled' WHERE symbol=%s AND reduce_only=true AND status='open'",(symbol,))
