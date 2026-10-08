from datetime import datetime, timezone
import pandas as pd

class MarketData:
    """Normalize CCXT OHLCV into a cached, Kronos-ready UTC DataFrame."""
    def __init__(self, exchange, ttl_seconds=30): self.exchange=exchange; self.ttl=ttl_seconds; self.cache={}
    def fetch(self, symbol, timeframe='15m', limit=512):
        key=(symbol,timeframe,limit); now=datetime.now(timezone.utc).timestamp()
        hit=self.cache.get(key)
        if hit and now-hit[0] < self.ttl: return hit[1].copy()
        rows=self.exchange.fetch_ohlcv(symbol,timeframe=timeframe,limit=limit)
        df=pd.DataFrame(rows,columns=['timestamp','open','high','low','close','volume'])
        df['timestamp']=pd.to_datetime(df['timestamp'],unit='ms',utc=True)
        for col in ('open','high','low','close','volume'): df[col]=pd.to_numeric(df[col],errors='coerce')
        df=df.dropna().sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
        if len(df)<2: raise ValueError('insufficient normalized OHLCV')
        self.cache[key]=(now,df.copy()); return df

    def correlation(self, symbol_a, symbol_b, timeframe='1h'):
        import numpy as np
        a=self.fetch(symbol_a,timeframe,limit=64).close.pct_change().dropna()
        b=self.fetch(symbol_b,timeframe,limit=64).close.pct_change().dropna()
        merged=pd.concat([a.reset_index(drop=True),b.reset_index(drop=True)],axis=1).dropna()
        if len(merged)<10: return 0.0
        value=float(merged.iloc[:,0].corr(merged.iloc[:,1]))
        return value if np.isfinite(value) else 0.0
