# Furry bot

Телеграм-бот: присылаешь селфи — в ответ приходит твоя фури-версия.
Картинку рисует OpenAI image edit (`gpt-image-2` по умолчанию). Зверя бот выбирает сам;
можно указать его в подписи к фото («волк», «лиса», «кот»).

## Запуск

1. Создай бота у [@BotFather](https://t.me/BotFather), получи токен.
2. Возьми ключ OpenAI на <https://platform.openai.com/api-keys>.
3. Запусти:

```bash
cd furry_bot
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=...  OPENAI_API_KEY=...
python bot.py
```

Или в Docker:

```bash
docker build -t furry-bot furry_bot
docker run -d --restart=always -e TELEGRAM_BOT_TOKEN=... -e OPENAI_API_KEY=... furry-bot
```

Необязательные переменные: `FURRY_MODEL` (например `gpt-image-1-mini` — дешевле),
`FURRY_QUALITY` (`low` / `medium` / `high`).

## Тесты

```bash
pip install pytest && python -m pytest furry_bot/tests
```
