"""
Telegram Session Checker Bot (Aiogram 3 + Telethon)
===================================================
Бот и скрипт для проверки Telegram .session файлов (по отдельности или в .zip).
"""
import os
import sys
import glob
import shutil
import zipfile
import asyncio
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
)
logger = logging.getLogger("SessionChecker")

# ----------------- КОНФИГУРАЦИЯ ПО УМОЛЧАНИЮ -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8697905230:AAFTMruUSZO09mSxskidhvsp7MnTTty4Byo")
DEFAULT_API_ID = int(os.getenv("API_ID", "2040"))
DEFAULT_API_HASH = os.getenv("API_HASH", "b18441a1ff607e10a989891a5462e627")
MAX_SESSIONS_PER_BATCH = 100
CONCURRENCY_LIMIT = 5

# ----------------- ПРОВЕРКА БИБЛИОТЕК -----------------
try:
    from telethon import TelegramClient
    from telethon.tl.functions.account import GetAuthorizationsRequest
    from telethon.errors import (
        AuthKeyUnregisteredError,
        UserDeactivatedError,
        UserDeactivatedBanError,
        SessionPasswordNeededError,
        SessionRevokedError,
        FloodWaitError
    )
except ImportError:
    logger.error("Библиотека 'telethon' не установлена. Установите: pip install telethon")

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from aiogram import Bot, Dispatcher, types, F
    from aiogram.filters import CommandStart
    from aiogram.types import FSInputFile
except ImportError:
    Bot, Dispatcher, types, F = None, None, None, None


class SessionCheckerEngine:
    """Ядро проверки сессий через MTProto API Telegram (Telethon)"""
    def __init__(self, api_id: int = DEFAULT_API_ID, api_hash: str = DEFAULT_API_HASH, concurrency: int = CONCURRENCY_LIMIT):
        self.api_id = api_id
        self.api_hash = api_hash
        self.semaphore = asyncio.Semaphore(concurrency)

    async def check_single_session(self, session_path: str) -> Dict[str, Any]:
        session_file = Path(session_path)
        session_str = str(session_file.with_suffix(''))

        result: Dict[str, Any] = {
            "file_name": session_file.name,
            "status": "Unknown",
            "is_valid": False,
            "error_message": None,
            "user_id": None,
            "phone": None,
            "username": None,
            "first_name": None,
            "total_sessions_count": 0,
            "other_sessions_count": 0,
            "authorizations": []
        }

        async with self.semaphore:
            client = TelegramClient(session_str, self.api_id, self.api_hash)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    result["status"] = "Unauthorized / Revoked"
                    result["error_message"] = "Сессия не авторизована или отозвана"
                    return result

                me = await client.get_me()
                if not me:
                    result["status"] = "Empty User"
                    result["error_message"] = "Не удалось получить профиль пользователя"
                    return result

                result["is_valid"] = True
                result["status"] = "Active / Valid"
                result["user_id"] = me.id
                result["phone"] = f"+{me.phone}" if me.phone else "N/A"
                result["username"] = f"@{me.username}" if me.username else "N/A"
                result["first_name"] = me.first_name or "N/A"

                auths_resp = await client(GetAuthorizationsRequest())
                authorizations_list = auths_resp.authorizations

                result["total_sessions_count"] = len(authorizations_list)
                result["other_sessions_count"] = max(0, len(authorizations_list) - 1)

                auth_records = []
                for auth in authorizations_list:
                    date_active_dt = auth.date_active
                    date_created_dt = auth.date_created
                    date_active_str = date_active_dt.strftime("%Y-%m-%d %H:%M:%S UTC") if date_active_dt else "N/A"
                    date_created_str = date_created_dt.strftime("%Y-%m-%d %H:%M:%S UTC") if date_created_dt else "N/A"

                    record = {
                        "is_current": getattr(auth, 'current', False),
                        "hash": getattr(auth, 'hash', None),
                        "device_model": getattr(auth, 'device_model', 'Unknown device'),
                        "platform": getattr(auth, 'platform', 'Unknown platform'),
                        "system_version": getattr(auth, 'system_version', ''),
                        "app_name": getattr(auth, 'app_name', 'Telegram App'),
                        "app_version": getattr(auth, 'app_version', ''),
                        "ip": getattr(auth, 'ip', 'N/A'),
                        "country": getattr(auth, 'country', 'N/A'),
                        "region": getattr(auth, 'region', 'N/A'),
                        "date_created": date_created_str,
                        "date_active": date_active_str,
                        "raw_date_active": date_active_dt
                    }
                    auth_records.append(record)

                auth_records.sort(
                    key=lambda x: (not x["is_current"], -(x["raw_date_active"].timestamp() if x["raw_date_active"] else 0))
                )
                result["authorizations"] = auth_records

            except (AuthKeyUnregisteredError, SessionRevokedError):
                result["status"] = "Dead / Revoked"
                result["error_message"] = "Ключ авторизации недействителен (сессия удалена)"
            except (UserDeactivatedError, UserDeactivatedBanError):
                result["status"] = "Banned / Deactivated"
                result["error_message"] = "Аккаунт заблокирован или удален Telegram"
            except SessionPasswordNeededError:
                result["status"] = "2FA Password Needed"
                result["error_message"] = "Требуется пароль двухфакторной аутентификации"
            except FloodWaitError as e:
                result["status"] = "FloodWait"
                result["error_message"] = f"Лимит запросов Telegram, ожидание {e.seconds} сек."
            except Exception as e:
                result["status"] = "Error"
                result["error_message"] = f"{type(e).__name__}: {str(e)}"
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass
        return result

    async def check_multiple_sessions(self, session_files: List[str], progress_callback=None) -> List[Dict[str, Any]]:
        tasks = []
        total = len(session_files)
        completed = 0

        async def worker(f_path):
            nonlocal completed
            res = await self.check_single_session(f_path)
            completed += 1
            if progress_callback:
                try:
                    await progress_callback(completed, total)
                except Exception:
                    pass
            return res

        for f in session_files:
            tasks.append(worker(f))
        return list(await asyncio.gather(*tasks))


def format_single_result_text(data: Dict[str, Any]) -> str:
    if not data["is_valid"]:
        return (
            f"❌ <b>Файл:</b> <code>{data['file_name']}</code>\n"
            f"<b>Статус:</b> <code>{data['status']}</code>\n"
            f"<b>Причина:</b> {data['error_message'] or 'Ошибка подключения'}"
        )

    lines = [
        f"📱 <b>Отчет по сессии:</b> <code>{data['file_name']}</code>",
        f"👤 <b>Пользователь:</b> {data['first_name']} ({data['username']})",
        f"📞 <b>Телефон:</b> <code>{data['phone']}</code> | <b>ID:</b> <code>{data['user_id']}</code>",
        f"📊 <b>Всего сессий на аккаунте:</b> <b>{data['total_sessions_count']}</b> "
        f"(включая эту: 1, сторонних: {data['other_sessions_count']})",
        "",
        "<b>Список авторизованных сессий:</b>"
    ]
    for idx, auth in enumerate(data["authorizations"], start=1):
        flag = "🟢 [ТЕКУЩАЯ СЕССИЯ]" if auth["is_current"] else "⚪ [ДРУГАЯ СЕССИЯ]"
        lines.append(
            f"\n<b>{idx}. {flag}</b>\n"
            f"• <b>Устройство:</b> {auth['device_model']} ({auth['platform']} {auth['system_version']})\n"
            f"• <b>Приложение:</b> {auth['app_name']} {auth['app_version']}\n"
            f"• <b>IP и страна:</b> <code>{auth['ip']}</code> ({auth['country']}, {auth['region']})\n"
            f"• <b>Создана:</b> <code>{auth['date_created']}</code>\n"
            f"• <b>Активность (в сети):</b> <b><code>{auth['date_active']}</code></b>"
        )
    return "\n".join(lines)


def generate_excel_report(results: List[Dict[str, Any]], output_filepath: str):
    accounts_rows = []
    sessions_rows = []
    for res in results:
        accounts_rows.append({
            "Файл": res["file_name"],
            "Статус": res["status"],
            "Валидна": "Да" if res["is_valid"] else "Нет",
            "Телефон": res["phone"],
            "Username": res["username"],
            "Имя": res["first_name"],
            "User ID": res["user_id"],
            "Всего сессий на аккаунте": res["total_sessions_count"],
            "Сторонних сессий": res["other_sessions_count"],
            "Ошибка/Детали": res["error_message"]
        })
        if res["is_valid"]:
            for auth in res["authorizations"]:
                sessions_rows.append({
                    "Файл аккаунта": res["file_name"],
                    "Телефон": res["phone"],
                    "Username": res["username"],
                    "Тип сессии": "Текущая (входной файл)" if auth["is_current"] else "Сторонняя / Другая",
                    "Устройство": auth["device_model"],
                    "Платформа": auth["platform"],
                    "ОС": auth["system_version"],
                    "Приложение": auth["app_name"],
                    "Версия": auth["app_version"],
                    "IP": auth["ip"],
                    "Страна": auth["country"],
                    "Регион": auth["region"],
                    "Дата создания": auth["date_created"],
                    "Последняя активность (в сети)": auth["date_active"]
                })

    df_accounts = pd.DataFrame(accounts_rows)
    df_sessions = pd.DataFrame(sessions_rows)
    with pd.ExcelWriter(output_filepath, engine="openpyxl") as writer:
        df_accounts.to_excel(writer, sheet_name="Аккаунты", index=False)
        if not df_sessions.empty:
            df_sessions.to_excel(writer, sheet_name="Активные сессии", index=False)
        else:
            pd.DataFrame([{"Инфо": "Нет валидных активных сессий"}]).to_excel(writer, sheet_name="Активные сессии", index=False)


def create_bot_app():
    if Bot is None:
        raise RuntimeError("Aiogram 3 не установлен! Запустите: pip install aiogram")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    engine = SessionCheckerEngine()

    @dp.message(CommandStart())
    async def cmd_start(message: types.Message):
        await message.answer(
            "👋 <b>Привет! Я бот для проверки Telegram-сессий.</b>\n\n"
            "🔍 <b>Что я умею:</b>\n"
            "1. Заходить в сессию и определять, живой ли аккаунт.\n"
            "2. Показывать общее количество сессий на аккаунте.\n"
            "3. Показывать активность каждой сессии (устройство, IP, время в сети).\n"
            "4. Разделять и присылать архивы: чистые (1 сессия) и подозрительные (>1 сессии).\n\n"
            "Отправьте мне <code>.session</code> файл или <code>.zip</code> архив!",
            parse_mode="HTML"
        )

    @dp.message(F.document)
    async def handle_document(message: types.Message):
        doc = message.document
        if not (doc.file_name.endswith(".session") or doc.file_name.endswith(".zip")):
            await message.reply("❌ Пожалуйста, отправьте файл с расширением <code>.session</code> или <code>.zip</code> архив.", parse_mode="HTML")
            return

        work_dir = Path(f"temp_{message.chat.id}_{int(datetime.now().timestamp())}")
        work_dir.mkdir(parents=True, exist_ok=True)
        status_msg = await message.reply("⏳ Загружаю файл...")

        try:
            download_path = work_dir / doc.file_name
            await bot.download(doc, destination=download_path)

            if doc.file_name.endswith(".zip"):
                with zipfile.ZipFile(download_path, 'r') as z:
                    z.extractall(work_dir)
                all_sessions = list(work_dir.rglob("*.session"))
                if not all_sessions:
                    await status_msg.edit_text("❌ В архиве не найдено файлов <code>.session</code>.", parse_mode="HTML")
                    return
                session_files = [str(p) for p in all_sessions[:MAX_SESSIONS_PER_BATCH]]
            else:
                session_files = [str(download_path)]

            total_count = len(session_files)
            last_update_time = [datetime.now()]

            async def progress(done, total):
                now = datetime.now()
                if (now - last_update_time[0]).total_seconds() >= 2.0 or done == total:
                    last_update_time[0] = now
                    try:
                        await status_msg.edit_text(f"🔄 <b>Проверка сессий:</b> {done}/{total}...\nПожалуйста, подождите.", parse_mode="HTML")
                    except Exception:
                        pass

            await status_msg.edit_text(f"🚀 Начинаю проверку {total_count} сессий...", parse_mode="HTML")
            results = await engine.check_multiple_sessions(session_files, progress_callback=progress)

            # Папки для сортировки файлов
            single_session_dir = work_dir / "single_session"
            multi_session_dir = work_dir / "multi_sessions"
            single_session_dir.mkdir(exist_ok=True)
            multi_session_dir.mkdir(exist_ok=True)

            single_count = 0
            multi_count = 0

            # Распределение исходных .session файлов по папкам
            for res, s_path in zip(results, session_files):
                source_file = Path(s_path)
                if not source_file.exists():
                    continue

                if res["is_valid"]:
                    if res["total_sessions_count"] == 1:
                        shutil.copy2(source_file, single_session_dir / source_file.name)
                        single_count += 1
                    else:
                        shutil.copy2(source_file, multi_session_dir / source_file.name)
                        multi_count += 1

            if total_count == 1:
                text = format_single_result_text(results[0])
                if len(text) > 4000:
                    text = text[:3900] + "\n\n<i>[Сообщение сокращено из-за лимита Telegram]</i>"
                await status_msg.edit_text(text, parse_mode="HTML")
            else:
                valid_count = sum(1 for r in results if r["is_valid"])
                invalid_count = total_count - valid_count
                summary_text = (
                    f"✅ <b>Проверка завершена!</b>\n\n"
                    f"📦 Всего проверено файлов: <b>{total_count}</b>\n"
                    f"🟢 Живых / Валидных: <b>{valid_count}</b>\n"
                    f"🔴 Невалидных / Ошибок: <b>{invalid_count}</b>\n\n"
                    f"🔒 <b>Только 1 сессия (чистые):</b> <b>{single_count}</b> шт.\n"
                    f"⚠️ <b>Много сессий (>1):</b> <b>{multi_count}</b> шт."
                )
                await status_msg.edit_text(summary_text, parse_mode="HTML")

                if pd is not None:
                    excel_path = work_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                    generate_excel_report(results, str(excel_path))
                    await message.reply_document(
                        FSInputFile(str(excel_path), filename=excel_path.name),
                        caption=f"📑 Excel-отчет ({total_count} сессий)"
                    )

            # Отправка архива с 1 сессией
            if single_count > 0:
                zip_single_path = work_dir / "single_sessions.zip"
                with zipfile.ZipFile(zip_single_path, "w", zipfile.ZIP_DEFLATED) as z:
                    for f in single_session_dir.glob("*.session"):
                        z.write(f, arcname=f.name)
                await message.reply_document(
                    FSInputFile(str(zip_single_path), filename="single_sessions.zip"),
                    caption=f"🟢 <b>Сессии с 1 подключением</b> ({single_count} шт.)",
                    parse_mode="HTML"
                )

            # Отправка архива с несколькими сессиями
            if multi_count > 0:
                zip_multi_path = work_dir / "multi_sessions.zip"
                with zipfile.ZipFile(zip_multi_path, "w", zipfile.ZIP_DEFLATED) as z:
                    for f in multi_session_dir.glob("*.session"):
                        z.write(f, arcname=f.name)
                await message.reply_document(
                    FSInputFile(str(zip_multi_path), filename="multi_sessions.zip"),
                    caption=f"⚠️ <b>Сессии с несколькими подключениями</b> ({multi_count} шт.)",
                    parse_mode="HTML"
                )

        except Exception as e:
            logger.exception("Ошибка при обработке файлов:")
            await status_msg.edit_text(f"❌ Произошла ошибка: <code>{str(e)}</code>", parse_mode="HTML")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    return bot, dp


async def run_cli(args):
    engine = SessionCheckerEngine(concurrency=args.threads)
    session_files = []
    temp_dir = None

    if args.zip:
        temp_dir = Path("temp_cli_zip")
        temp_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(args.zip, 'r') as z:
            z.extractall(temp_dir)
        session_files = [str(p) for p in temp_dir.rglob("*.session")][:MAX_SESSIONS_PER_BATCH]
    elif args.folder:
        session_files = [str(p) for p in Path(args.folder).glob("*.session")][:MAX_SESSIONS_PER_BATCH]
    elif args.file:
        session_files = [args.file]

    if not session_files:
        print("❌ Не найдено .session файлов для проверки.")
        return

    print(f"🚀 Запуск проверки {len(session_files)} сессий...")
    results = await engine.check_multiple_sessions(session_files)

    print("\n" + "=" * 50)
    for r in results:
        print(f"\nФайл: {r['file_name']} | Статус: {r['status']}")
        if r['is_valid']:
            print(f" Аккаунт: {r['first_name']} ({r['username']}) {r['phone']} [ID: {r['user_id']}]")
            print(f" Всего сессий: {r['total_sessions_count']} (текущая: 1, сторонних: {r['other_sessions_count']})")
        else:
            print(f" Ошибка: {r['error_message']}")
    print("=" * 50)

    if args.output and pd is not None:
        generate_excel_report(results, args.output)
        print(f"\n📑 Отчет сохранен в файл: {args.output}")

    if temp_dir:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Telegram Session Checker (Bot & CLI)")
    parser.add_argument("--cli", action="store_true", help="Запустить в консольном режиме")
    parser.add_argument("--file", type=str, help="Путь к одному .session файлу")
    parser.add_argument("--folder", type=str, help="Путь к папке с .session файлами")
    parser.add_argument("--zip", type=str, help="Путь к .zip архиву с сессиями")
    parser.add_argument("--output", type=str, default="sessions_report.xlsx", help="Путь к выгружаемому Excel файлу")
    parser.add_argument("--threads", type=int, default=5, help="Количество параллельных потоков")
    args = parser.parse_args()

    if args.cli or args.file or args.folder or args.zip:
        asyncio.run(run_cli(args))
    else:
        if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" and not os.getenv("BOT_TOKEN"):
            print("⚠️ BOT_TOKEN не указан в переменных окружения!")
            sys.exit(1)
        print("🤖 Запуск Telegram-бота...")
        bot, dp = create_bot_app()
        asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    main()
