import time
import hmac
import hashlib
import os
import requests
from urllib.parse import urlencode

API_KEY = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
TESTNET = os.environ.get("BINANCE_TESTNET", "true").lower() == "true"

BASE_URL = "https://testnet.binancefuture.com" if TESTNET else "https://fapi.binance.com"
SPOT_URL = "https://testnet.binance.vision" if TESTNET else "https://api.binance.com"

class BinanceError(Exception):
    pass

def _sign(params: dict) -> dict:
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    query = urlencode(params)
    signature = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = signature
    return params

def _headers(api_key=None):
    key = api_key if api_key else API_KEY
    return {"X-MBX-APIKEY": key}

def _request(method, path, params=None, signed=True, base_url=None, api_key=None, api_secret=None):
    params = params or {}
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urlencode(params)
        sig_secret = api_secret if api_secret else API_SECRET
        signature = hmac.new(sig_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = signature
    url = f"{base_url or BASE_URL}{path}"
    resp = requests.request(method, url, params=params, headers=_headers(api_key), timeout=10)
    if resp.status_code >= 400:
        raise BinanceError(f"{resp.status_code}: {resp.text}")
    return resp.json()

def get_account_balance_usdt():
    data = _request("GET", "/fapi/v2/account")
    return float(data.get("totalMarginBalance", 0))

def get_position_risk(symbol=None):
    params = {"symbol": symbol} if symbol else {}
    return _request("GET", "/fapi/v2/positionRisk", params)

def get_open_orders(symbol=None):
    params = {"symbol": symbol} if symbol else {}
    return _request("GET", "/fapi/v1/openOrders", params)

def create_listen_key():
    data = _request("POST", "/fapi/v1/listenKey", signed=False)
    return data["listenKey"]

def keepalive_listen_key(listen_key):
    return _request("PUT", "/fapi/v1/listenKey", signed=False)

def place_stop_market_order(symbol, side, stop_price, quantity, reduce_only=True):
    params = {
        "symbol": symbol,
        "side": side,
        "type": "STOP_MARKET",
        "stopPrice": round(stop_price, 6),
        "quantity": quantity,
        "reduceOnly": "true" if reduce_only else "false",
        "workingType": "MARK_PRICE",
    }
    return _request("POST", "/fapi/v1/order", params)

def cancel_order(symbol, order_id):
    return _request("DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id})

def close_position_market(symbol, side, quantity):
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": quantity,
        "reduceOnly": "true",
    }
    return _request("POST", "/fapi/v1/order", params)

def close_all_positions():
    positions = get_position_risk()
    closed = []
    for p in positions:
        amt = float(p.get("positionAmt", 0))
        if amt == 0:
            continue
        symbol = p["symbol"]
        side = "SELL" if amt > 0 else "BUY"
        qty = abs(amt)
        try:
            close_position_market(symbol, side, qty)
            closed.append(symbol)
        except BinanceError:
            pass
    return closed

def cancel_all_open_orders():
    positions = get_position_risk()
    symbols = {p["symbol"] for p in positions if float(p.get("positionAmt", 0)) != 0}
    for symbol in symbols:
        try:
            _request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
        except BinanceError:
            pass

def get_mark_price(symbol):
    data = _request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol}, signed=False)
    return float(data["markPrice"])

def transfer_to_wallet(coin, amount, withdrawal_key, withdrawal_secret):
    """تحويل من Futures إلى Spot"""
    params = {
        "coin": coin,
        "amount": amount,
        "type": "MAIN_UMFUTURE",
    }
    return _request("POST", "/sapi/v1/asset/transfer", params, 
                   base_url=SPOT_URL, api_key=withdrawal_key, api_secret=withdrawal_secret)

def get_spot_balance(asset="USDT", withdrawal_key=None, withdrawal_secret=None):
    """احصل على رصيد Spot"""
    params = {"asset": asset}
    key = withdrawal_key if withdrawal_key else API_KEY
    secret = withdrawal_secret if withdrawal_secret else API_SECRET
    data = _request("GET", "/api/v3/account", params, base_url=SPOT_URL, api_key=key, api_secret=secret)
    for bal in data.get("balances", []):
        if bal["asset"] == asset:
            return float(bal["free"])
    return 0.0
