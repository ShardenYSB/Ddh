# Ddh — Telegram Session Checker

Python 3.10+; проверка собственных Telegram `.session` файлов через CLI или бота.

## Установка и запуск

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN='токен из BotFather'
python app.py
```

Токен задаётся через переменную окружения `BOT_TOKEN`. Файл `.env` автоматически не загружается. Если ранее опубликованный токен отозван, создайте новый через BotFather. Для своих реквизитов Telegram API задайте `API_ID` и `API_HASH` (my.telegram.org).

CLI работает без токена бота:

```sh
python app.py --file account.session
python app.py --zip sessions.zip --threads 5 --output report.xlsx
python app.py --folder ./sessions
```

Количество потоков должно быть больше нуля. На каждый сетевой запрос установлен тайм-аут; обработка большого архива может занять несколько минут. Один токен должен обслуживаться одним процессом polling.

## Проверки

```sh
python -m unittest discover -s tests -v
```

## Railway: Unauthorized при запуске

`TelegramUnauthorizedError: Telegram server says - Unauthorized` на вызове `get_me` означает, что Telegram не принимает токен бота.

1. В Telegram откройте @BotFather и получите действующий токен нужного бота (`/token`; для замены опубликованного токена — `/revoke`).
2. Railway → сервис бота → Variables → `BOT_TOKEN`: укажите только токен, без кавычек, пробелов и префикса `bot`. Переменная окружения имеет приоритет над значением в старом исходнике.
3. Примените изменения и выполните Redeploy.

В исходнике токен хранить не нужно. Обновление кода само по себе не исправляет отклонённый Telegram токен. Не публикуйте токен в чатах и логах.
