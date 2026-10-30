"""Load Kronos once; model failures are explicit degraded health, not a trading outage."""
import numpy as np
import torch
from loguru import logger
from config.settings import settings

class KronosForecaster:
    def __init__(self):
        self.model_name=settings.KRONOS_MODEL; self.tokenizer_name=settings.KRONOS_TOKENIZER
        self.device=('cuda' if torch.cuda.is_available() else 'cpu') if settings.KRONOS_DEVICE=='auto' else settings.KRONOS_DEVICE
        self.predictor=None; self.status='not_loaded'; self.version=self.model_name; self.error=None
    def load(self):
        try:
            from model import Kronos, KronosTokenizer, KronosPredictor
            tokenizer=KronosTokenizer.from_pretrained(self.tokenizer_name)
            model=Kronos.from_pretrained(self.model_name)
            model=model.to(self.device).eval()
            self.predictor=KronosPredictor(model,tokenizer,max_context=settings.KRONOS_MAX_CONTEXT)
            self.status='loaded'; logger.info('kronos_loaded model={} device={}',self.model_name,self.device); return True
        except Exception as e:
            self.status='degraded'; self.error=type(e).__name__; logger.warning('kronos_degraded error={}',self.error); return False
    def forecast(self, ohlcv_df, horizon=24):
        if self.status!='loaded': return {'available':False,'model_version':self.version,'status':self.status}
        df=ohlcv_df.rename(columns={'timestamp':'timestamps'}).copy()
        x_timestamp=df['timestamp'] if 'timestamp' in df else df['timestamps']
        df=df[['open','high','low','close','volume']].tail(settings.KRONOS_MAX_CONTEXT)
        y_timestamp=__import__('pandas').Series(__import__('pandas').date_range(start=x_timestamp.iloc[-1],periods=horizon+1,freq='15min',tz='UTC')[1:])
        pred=self.predictor.predict(df,x_timestamp=x_timestamp.tail(len(df)),y_timestamp=y_timestamp,pred_len=horizon, T=1.0, top_p=0.9, sample_count=1)
        closes=np.asarray(pred['close'],dtype=float)
        direction = 'long' if closes[-1] > float(df.close.iloc[-1]) else 'short'
        return {'available':True,'model_version':self.version,'status':self.status,'forecast_close':closes.tolist(),'direction':direction,'volatility_estimate':float(np.std(np.diff(np.log(closes+1e-12)))) if len(closes)>1 else 0.0}
    def volatility(self, ohlcv_df, horizon=24): return self.forecast(ohlcv_df,horizon).get('volatility_estimate')
    def synthetic_scenarios(self, ohlcv_df, count=10, horizon=24):
        if self.status!='loaded': return {'available':False,'model_version':self.version,'status':self.status,'scenarios':[]}
        import pandas as pd
        frame=ohlcv_df.copy(); x_timestamp=frame['timestamp']
        x=frame[['open','high','low','close','volume']].tail(settings.KRONOS_MAX_CONTEXT)
        x_timestamp=x_timestamp.tail(len(x))
        y_timestamp=pd.Series(pd.date_range(start=x_timestamp.iloc[-1],periods=horizon+1,freq='15min',tz='UTC')[1:])
        scenarios=[]
        for _ in range(max(1,min(count,100))):
            pred=self.predictor.predict(x,x_timestamp=x_timestamp,y_timestamp=y_timestamp,pred_len=horizon,T=1.0,top_p=0.9,sample_count=1)
            scenarios.append(np.asarray(pred['close'],dtype=float).tolist())
        return {'available':True,'model_version':self.version,'status':self.status,'scenarios':scenarios}
