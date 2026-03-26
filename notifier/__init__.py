from notifier.telegram import TelegramNotifier


def get_notifier(config) -> TelegramNotifier | None:
    """Return a TelegramNotifier if credentials are configured, else None."""
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        return TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    return None
