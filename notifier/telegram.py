"""Telegram notifier — sends a message for every trade fill."""
import requests


class TelegramNotifier:
    _API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str, chat_id: str):
        self._token = token
        self._chat_id = chat_id

    def send_trade(
        self,
        side: str,
        book: str,
        price: float,
        amount: float,
        pnl: float,
        fees: float,
        mode: str,
    ) -> None:
        pair = book.replace("_", "/").upper()
        action = "BUY" if side == "buy" else "SELL"
        text = (
            f"BitsyBot [{mode}]\n"
            f"{action} {pair}\n"
            f"Price:  ${price:,.2f} MXN\n"
            f"Amount: {amount:.6f}\n"
            f"P&L:    {pnl:+,.2f} MXN\n"
            f"Fees:   {fees:.2f} MXN"
        )
        try:
            requests.post(
                self._API_URL.format(token=self._token),
                json={"chat_id": self._chat_id, "text": text},
                timeout=5,
            )
        except Exception:
            pass  # Never crash the engine
