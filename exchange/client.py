"""
Bitso REST API client with HMAC-SHA256 authentication.
Docs: https://docs.bitso.com/bitso-api/docs
"""
import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import requests

from exchange.models import Balance, Candle, Order, Ticker, Trade


class BitsoClient:
    BASE_URL = "https://api.bitso.com"
    OHLC_URL = "https://bitso.com/api/v3/ohlc"

    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "BitsyBot/1.0"})

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _sign(self, method: str, path: str, payload: str = "") -> dict:
        nonce = str(int(time.time() * 1000))
        message = nonce + method.upper() + path + payload
        signature = hmac.new(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "Authorization": f"Bitso {self.api_key}:{nonce}:{signature}",
            "Content-Type": "application/json",
        }

    # ── Internal request helpers ──────────────────────────────────────────────

    def _get(self, path: str, params: Optional[dict] = None, auth: bool = False) -> dict:
        url = self.BASE_URL + path
        headers = {}
        if auth:
            query = "?" + urlencode(params) if params else ""
            headers = self._sign("GET", path + query)
        resp = self.session.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Bitso API error: {data}")
        return data["payload"]

    def _post(self, path: str, body: dict) -> dict:
        payload_str = json.dumps(body)
        headers = self._sign("POST", path, payload_str)
        resp = self.session.post(
            self.BASE_URL + path, data=payload_str, headers=headers, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Bitso API error: {data}")
        return data["payload"]

    def _delete(self, path: str) -> dict:
        headers = self._sign("DELETE", path)
        resp = self.session.delete(self.BASE_URL + path, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Bitso API error: {data}")
        return data["payload"]

    # ── Public endpoints ──────────────────────────────────────────────────────

    def get_ticker(self, book: str = "btc_mxn") -> Ticker:
        payload = self._get("/v3/ticker/", params={"book": book})
        return Ticker(
            book=payload["book"],
            last=float(payload["last"]),
            bid=float(payload["bid"]),
            ask=float(payload["ask"]),
            volume=float(payload["volume"]),
            timestamp=datetime.utcfromtimestamp(int(payload["created_at"]) / 1000)
            if payload.get("created_at")
            else datetime.utcnow(),
        )

    # Bitso OHLC expects time_bucket in seconds, not friendly strings
    BUCKET_MAP = {
        "1m": "60", "5m": "300", "15m": "900", "30m": "1800",
        "1h": "3600", "4h": "14400", "12h": "43200", "1d": "86400", "1w": "604800",
    }

    def get_candles(
        self,
        book: str = "btc_mxn",
        time_bucket: str = "1d",
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> list[Candle]:
        """
        Fetch OHLCV candles.
        time_bucket: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w (converted to seconds for API)
        start/end: Unix timestamps in seconds (converted to milliseconds for API)
        """
        bucket = self.BUCKET_MAP.get(time_bucket, time_bucket)
        params: dict = {"book": book, "time_bucket": bucket}
        if start:
            params["start"] = start * 1000
        if end:
            params["end"] = end * 1000
        resp = self.session.get(self.OHLC_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Bitso OHLC API error: {data}")
        payload = data["payload"]
        candles = []
        for c in payload:
            candles.append(
                Candle(
                    timestamp=datetime.utcfromtimestamp(int(c["bucket_start_time"]) / 1000),
                    open=float(c.get("first_rate") or c["open"]),
                    high=float(c.get("max_rate") or c["high"]),
                    low=float(c.get("min_rate") or c["low"]),
                    close=float(c.get("last_rate") or c["close"]),
                    volume=float(c["volume"]),
                )
            )
        return candles

    def get_order_book(self, book: str = "btc_mxn") -> dict:
        return self._get("/v3/order_book/", params={"book": book})

    # ── Private endpoints ─────────────────────────────────────────────────────

    def get_balances(self) -> dict[str, Balance]:
        payload = self._get("/v3/balance/", auth=True)
        balances = {}
        for b in payload["balances"]:
            balances[b["currency"]] = Balance(
                currency=b["currency"],
                available=float(b["available"]),
                locked=float(b["locked"]),
            )
        return balances

    def place_order(
        self,
        book: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
    ) -> Order:
        body: dict = {
            "book": book,
            "side": side,
            "type": order_type,
            "major": str(round(amount, 8)),
        }
        if order_type == "limit" and price is not None:
            body["price"] = str(round(price, 2))
        payload = self._post("/v3/orders/", body)
        return Order(
            order_id=payload["oid"],
            book=book,
            side=side,
            order_type=order_type,
            price=price or 0.0,
            amount=amount,
        )

    def cancel_order(self, order_id: str) -> bool:
        self._delete(f"/v3/orders/{order_id}/")
        return True

    def cancel_all_orders(self, book: str) -> bool:
        self._delete(f"/v3/orders/all/?book={book}")
        return True

    def get_open_orders(self, book: str) -> list[Order]:
        payload = self._get("/v3/open_orders/", params={"book": book}, auth=True)
        orders = []
        for o in payload:
            orders.append(
                Order(
                    order_id=o["oid"],
                    book=o["book"],
                    side=o["side"],
                    order_type=o["type"],
                    price=float(o.get("price", 0)),
                    amount=float(o["original_amount"]),
                    filled=float(o["filled_amount"]),
                    status=o["status"],
                    created_at=datetime.fromisoformat(o["created_at"].replace("Z", "+00:00")),
                )
            )
        return orders

    def get_user_trades(self, book: str, limit: int = 100) -> list[Trade]:
        payload = self._get(
            "/v3/user_trades/",
            params={"book": book, "limit": limit},
            auth=True,
        )
        trades = []
        for t in payload:
            trades.append(
                Trade(
                    trade_id=t["tid"],
                    book=t["book"],
                    side=t["side"],
                    price=float(t["price"]),
                    amount=float(t["major"]),
                    fees=float(t.get("fees_amount", 0)),
                    timestamp=datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")),
                )
            )
        return trades
