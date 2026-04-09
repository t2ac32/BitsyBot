"""Telegram notifier — trade alerts and /status command handler."""
import threading
import requests


class TelegramNotifier:
    _BASE = "https://api.telegram.org/bot{token}"

    # Optional GIF URLs shown in /status reply.
    # Set TELEGRAM_GIF_PROFIT / TELEGRAM_GIF_LOSS in .env,
    # or grab direct GIF links from giphy.com / tenor.com.
    GIF_PROFIT: str = ""
    GIF_LOSS: str = ""

    def __init__(self, token: str, chat_id: str,
                 gif_profit: str = "", gif_loss: str = ""):
        self._token = token
        self._chat_id = chat_id
        self._base = self._BASE.format(token=token)
        self.GIF_PROFIT = gif_profit
        self.GIF_LOSS = gif_loss
        self._offset: int = 0
        self._listening: bool = False
        self._thread: threading.Thread | None = None

    # ── Internal HTTP helper ───────────────────────────────────────────────

    def _post(self, method: str, payload: dict) -> None:
        try:
            requests.post(f"{self._base}/{method}", json=payload, timeout=5)
        except Exception:
            pass

    # ── Trade notification ─────────────────────────────────────────────────

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
        self._post("sendMessage", {"chat_id": self._chat_id, "text": text})

    # ── Status command ─────────────────────────────────────────────────────

    def send_status(self, stats: dict, mode: str) -> None:
        pnl = stats["total_pnl"]
        sign = "+" if pnl >= 0 else ""
        emoji = "📈" if pnl >= 0 else "📉"

        lines = [
            f"🤖 BitsyBot [{mode}] — online",
            "",
            f"{emoji} Performance",
            f"Total P&L:     {sign}{pnl:,.2f} MXN",
            f"Total trades:  {stats['total_trades']} ({stats['buys']} buys / {stats['sells']} sells)",
            "",
        ]

        if stats["last_buy"]:
            lb = stats["last_buy"]
            lines += [
                "🟢 Last Buy",
                f"  Price:  ${lb['price']:,.2f} MXN",
                f"  Amount: {lb['amount']:.6f}",
                f"  Date:   {lb['timestamp']}",
                "",
            ]
        else:
            lines += ["🟢 No buys recorded yet", ""]

        if stats["last_sell"]:
            ls = stats["last_sell"]
            lines += [
                "🔴 Last Sell",
                f"  Price:  ${ls['price']:,.2f} MXN",
                f"  Amount: {ls['amount']:.6f}",
                f"  Date:   {ls['timestamp']}",
            ]
        else:
            lines.append("🔴 No sells recorded yet")

        text = "\n".join(lines)
        gif_url = self.GIF_PROFIT if pnl >= 0 else self.GIF_LOSS

        if gif_url:
            self._post("sendAnimation", {
                "chat_id": self._chat_id,
                "animation": gif_url,
                "caption": text,
            })
        else:
            self._post("sendMessage", {"chat_id": self._chat_id, "text": text})

    # ── Command listener (background thread) ──────────────────────────────

    def start_command_listener(self, get_stats_fn, mode: str) -> None:
        """Start a daemon thread that polls for /status commands."""
        self._listening = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(get_stats_fn, mode),
            daemon=True,
            name="telegram-cmd-listener",
        )
        self._thread.start()

    def stop_command_listener(self) -> None:
        self._listening = False

    def _poll_loop(self, get_stats_fn, mode: str) -> None:
        while self._listening:
            try:
                resp = requests.post(
                    f"{self._base}/getUpdates",
                    json={
                        "offset": self._offset,
                        "timeout": 30,
                        "allowed_updates": ["message"],
                    },
                    timeout=35,
                ).json()

                for update in resp.get("result", []):
                    self._offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    # Only respond to the configured chat
                    if str(msg.get("chat", {}).get("id", "")) != self._chat_id:
                        continue
                    if msg.get("text", "").startswith("/status"):
                        self.send_status(get_stats_fn(), mode)
            except Exception:
                pass
