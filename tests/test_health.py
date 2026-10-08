import asyncio, unittest
from types import SimpleNamespace
from aiohttp.test_utils import make_mocked_request
from src.health import HealthServer

class Ledger:
    def __init__(self): self.enabled=False
    def health(self): return True
    def get_state(self,*a): return None
class HealthTests(unittest.TestCase):
    def test_200_requires_telegram_db_and_kronos_loaded_or_degraded(self):
        l=Ledger(); listener=SimpleNamespace(connected=True,client=SimpleNamespace(is_connected=lambda:True)); k=SimpleNamespace(status='degraded')
        server=HealthServer(l,listener,k,broker=SimpleNamespace(flatten_all=lambda:None))
        response=asyncio.run(server.health(make_mocked_request('GET','/health')))
        self.assertEqual(response.status,200)
        server_no_broker=HealthServer(l,listener,k)
        self.assertEqual(asyncio.run(server_no_broker.health(make_mocked_request('GET','/health'))).status,503)
        k.status='loading'
        response=asyncio.run(server.health(make_mocked_request('GET','/health')))
        self.assertEqual(response.status,503)
        k.status='loaded'; listener.connected=False
        self.assertEqual(asyncio.run(server.health(make_mocked_request('GET','/health'))).status,503)
        listener.connected=True; l.health=lambda:False
        self.assertEqual(asyncio.run(server.health(make_mocked_request('GET','/health'))).status,503)
