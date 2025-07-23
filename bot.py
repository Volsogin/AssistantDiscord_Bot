import os
import socket
import asyncio

import discord
import pyotp
from discord import Intents
from discord.ext import tasks
from dotenv import load_dotenv

# ─── Загрузка переменных окружения ───────────────────────────────────────────
load_dotenv()
TOKEN       = os.getenv("DISCORD_TOKEN")
SERVER_IP   = os.getenv("SERVER_IP")
SERVER_PORT = int(os.getenv("SERVER_PORT", 0))
TOTP_SECRET = os.getenv("TOTP_SECRET")  # base32-секрет для Google Authenticator
ALERT_USERS = [int(x) for x in os.getenv("ALERT_USERS", "").split(",") if x]
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))  # секунд
# ─────────────────────────────────────────────────────────────────────────────

intents = Intents.default()
intents.messages = True
bot = discord.Client(intents=intents)

# Состояние бота и сессий
last_status = None
awaiting_otp = {}      # user_id -> prompt_message
admin_sessions = set() # авторизованные user_id
admin_menus = {}       # user_id -> menu_message_id


def is_server_online(ip: str, port: int, timeout: float = 5.0) -> bool:
    """Пробуем открыть TCP-коннект. True=онлайн, False=офлайн."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((ip, port))
            return True
        except:
            return False


@tasks.loop(seconds=CHECK_INTERVAL)
async def check_server():
    global last_status
    status = is_server_online(SERVER_IP, SERVER_PORT)
    if last_status is None:
        last_status = status
        return
    if status != last_status:
        text = (
            f"🔔 Ваш Minecraft-сервер `{SERVER_IP}:{SERVER_PORT}` "
            + ("запущен ✅" if status else "выключен ❌")
        )
        for uid in ALERT_USERS:
            try:
                user = await bot.fetch_user(uid)
                await user.send(text)
            except Exception as e:
                print(f"Не удалось отправить DM {uid}: {e}")
        last_status = status


@bot.event
async def on_ready():
    print(f"Бот {bot.user} готов. Запускаю мониторинг…")
    check_server.start()


@bot.event
async def on_message(message: discord.Message):
    # Работаем только в личных сообщениях
    if message.author.bot or not isinstance(message.channel, discord.DMChannel):
        return

    uid = message.author.id
    text = message.content.strip()

    # Игнорируем авторизованных — они работают только через реакции
    if uid in admin_sessions:
        return

    # Запускаем процесс авторизации OTP
    if text == "/admin":
        prompt = await message.channel.send("🔒 Пожалуйста, введите 6-значный секретный код:")
        awaiting_otp[uid] = prompt
        return

    # Обработка кода OTP
    if uid in awaiting_otp:
        prompt_msg = awaiting_otp.pop(uid)
        # Удаляем запрос бота
        try:
            await prompt_msg.delete()
        except:
            pass
        # Удаляем сообщение пользователя
        try:
            await message.delete()
        except:
            pass

        # Проверяем 6-значный код
        totp = pyotp.TOTP(TOTP_SECRET)
        if totp.verify(text):
            admin_sessions.add(uid)
            menu = await message.channel.send(
                "Здравствуйте, Алексей Сергеевич.**\n"
                "🛠 **Админ-панель\n"
                "🌍 — статус сервера\n"
                "❌ — выход"
            )
            # Ставим реакции
            await menu.add_reaction("🌍")
            await menu.add_reaction("❌")
            admin_menus[uid] = menu.id
        else:
            err = await message.channel.send("❌ Неверный код. Попробуйте снова.")
            await asyncio.sleep(5)
            try:
                await err.delete()
            except:
                pass
        return


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    # Игнорируем реакции бота
    if payload.user_id == bot.user.id:
        return

    # Проверяем, что пользователь в админ-сессии
    if payload.user_id not in admin_menus:
        return
    if payload.message_id != admin_menus[payload.user_id]:
        return

    # Получаем пользователя и DM-канал
    try:
        user = await bot.fetch_user(payload.user_id)
        channel = user.dm_channel or await user.create_dm()
    except:
        return

    emoji = str(payload.emoji)
    if emoji == "🌍":
        status = is_server_online(SERVER_IP, SERVER_PORT)
        await user.send(
            f"🌐 Сервер `{SERVER_IP}:{SERVER_PORT}` "
            + ("онлайн ✅" if status else "офлайн ❌")
        )
    elif emoji == "❌":
        # Удаляем меню
        try:
            msg = await channel.fetch_message(payload.message_id)
            await msg.delete()
        except:
            pass
        # Завершаем сессию
        admin_sessions.discard(payload.user_id)
        admin_menus.pop(payload.user_id, None)
        await user.send("▶️ Вы вышли из админ-панели.")


if __name__ == "__main__":
    bot.run(TOKEN)
