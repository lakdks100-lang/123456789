import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import time
import aiohttp
import os
import re
from aiohttp import web

# 봇 기본 설정
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

DB_NAME = 'partner_bot.db'
BACKUP_CHANNEL_ID = 1537622135173021756 # 유저님이 지정하신 백업 전용 채널 ID

def init_db():
    """DB 초기화 및 테이블 생성"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS webhooks
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER, 
                      webhook_url TEXT, 
                      webhook_name TEXT,
                      message TEXT, 
                      interval_hours INTEGER, 
                      last_sent REAL)''')
        conn.commit()

async def backup_db(bot_instance):
    """지정된 백업 채널로 DB 파일을 다운로드 형태로 무한 유지 백업"""
    channel = bot_instance.get_channel(BACKUP_CHANNEL_ID)
    if channel:
        try:
            timestamp = int(time.time())
            backup_filename = f"partner_db_backup_{timestamp}.db"
            
            await channel.send(
                content=f"💾 **DB 자동 백업 완료** (Timestamp: {timestamp})", 
                file=discord.File(DB_NAME, filename=backup_filename)
            )
            print(f"[백업 성공] {backup_filename} 업로드 완료.")
        except Exception as e:
            print(f"[백업 실패] 채널 전송 중 오류 발생: {e}")

class IntervalView(discord.ui.View):
    """메시지 주기 설정을 위한 1h, 12h, 24h 버튼 임베드 UI"""
    def __init__(self, user_id: int, webhook_url: str):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.webhook_url = webhook_url

    async def update_interval(self, interaction: discord.Interaction, hours: int):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 본인이 등록한 웹훅의 주기만 설정할 수 있습니다.", ephemeral=True)
            return
            
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("UPDATE webhooks SET interval_hours = ? WHERE webhook_url = ? AND user_id = ?", 
                      (hours, self.webhook_url, self.user_id))
            conn.commit()
            
        await interaction.response.send_message(f"✅ 설정 완료! 이제 이 웹훅은 **{hours}시간** 단위로 메시지를 발송합니다.", ephemeral=True)

    @discord.ui.button(label="1시간 (1h)", style=discord.ButtonStyle.primary, custom_id="btn_1h")
    async def btn_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_interval(interaction, 1)
        
    @discord.ui.button(label="12시간 (12h)", style=discord.ButtonStyle.secondary, custom_id="btn_12h")
    async def btn_12(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_interval(interaction, 12)
        
    @discord.ui.button(label="24시간 (24h)", style=discord.ButtonStyle.success, custom_id="btn_24h")
    async def btn_24(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_interval(interaction, 24)

@bot.event
async def on_ready():
    init_db()
    print(f"✅ 봇 로그인 완료: {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ 슬래시 명령어 {len(synced)}개 전역 동기화 완료.")
    except Exception as e:
        print(f"❌ 동기화 에러: {e}")
        
    if not webhook_sender_loop.is_running():
        webhook_sender_loop.start()

# ----------------- 슬래시 명령어 세팅 -----------------

@bot.tree.command(name="웹훅설정", description="새로운 파트너 웹훅 URL을 등록합니다.")
@app_commands.default_permissions(administrator=True)
async def set_webhook(interaction: discord.Interaction, 웹훅_url: str):
    if not re.match(r"^https://(?:ptb\.|canary\.)?discord\.com/api/webhooks/\d+/[a-zA-Z0-9_-]+$", 웹훅_url):
        await interaction.response.send_message("❌ **올바른 디스코드 웹훅 URL이 아닙니다.**", ephemeral=True)
        return

    user_id = interaction.user.id
    current_time = time.time()
    webhook_name = "알 수 없는 채널"

    # 웹훅 정보(채널 이름 등)를 디스코드 API에서 읽어와서 저장
    try:
        async with aiohttp.ClientSession() as session:
            webhook = discord.Webhook.from_url(웹훅_url, session=session)
            fetched_webhook = await webhook.fetch()
            if fetched_webhook.name:
                webhook_name = fetched_webhook.name
    except Exception:
        pass # 읽어오기 실패해도 등록은 진행
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        # 이미 존재하는 웹훅인지 확인
        c.execute("SELECT id FROM webhooks WHERE webhook_url = ? AND user_id = ?", (웹훅_url, user_id))
        if c.fetchone():
            await interaction.response.send_message("⚠️ **이미 등록된 웹훅입니다.**", ephemeral=True)
            return

        c.execute("INSERT INTO webhooks (user_id, webhook_url, webhook_name, message, interval_hours, last_sent) VALUES (?, ?, ?, ?, ?, ?)", 
                  (user_id, 웹훅_url, webhook_name, "메시지가 설정되지 않았습니다.", 0, current_time))
        conn.commit()
    
    await interaction.response.send_message(f"✅ **웹훅이 등록되었습니다.** (인식된 이름: `{webhook_name}`)\n`/메시지설정` 명령어로 내용을 작성해주세요.", ephemeral=True)
    await backup_db(bot)

@bot.tree.command(name="메시지설정", description="등록된 웹훅에 발송할 메시지 내용을 설정합니다.")
@app_commands.default_permissions(administrator=True)
async def set_message(interaction: discord.Interaction, 웹훅_url: str, 메시지: str):
    user_id = interaction.user.id
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM webhooks WHERE webhook_url = ? AND user_id = ?", (웹훅_url, user_id))
        if not c.fetchone():
            await interaction.response.send_message("❌ **DB에서 해당 웹훅을 찾을 수 없습니다.** 먼저 `/웹훅설정`을 해주세요.", ephemeral=True)
            return
            
        c.execute("UPDATE webhooks SET message = ? WHERE webhook_url = ? AND user_id = ?", (메시지, 웹훅_url, user_id))
        conn.commit()
        
    await interaction.response.send_message("✅ **해당 웹훅의 메시지가 성공적으로 저장되었습니다.**", ephemeral=True)

@bot.tree.command(name="웹훅주기", description="임베드 버튼을 통해 웹훅의 자동 발송 주기를 설정합니다.")
@app_commands.default_permissions(administrator=True)
async def set_interval(interaction: discord.Interaction, 웹훅_url: str):
    user_id = interaction.user.id
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT webhook_name FROM webhooks WHERE webhook_url = ? AND user_id = ?", (웹훅_url, user_id))
        result = c.fetchone()
    
    if not result:
        await interaction.response.send_message("❌ **해당 웹훅을 찾을 수 없습니다.**", ephemeral=True)
        return
        
    webhook_name = result[0]
    
    embed = discord.Embed(title="⏱️ 웹훅 발송 주기 설정", description=f"**대상 채널/웹훅:** `{webhook_name}`\n\n아래 버튼을 눌러 이 웹훅에 메시지를 보낼 시간 단위를 선택해주세요.", color=0x3498db)
    view = IntervalView(user_id, 웹훅_url)
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="웹훅확인", description="현재 연동된 웹훅 개수와 상세 정보를 확인합니다.")
@app_commands.default_permissions(administrator=True)
async def check_webhooks(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT webhook_name, interval_hours, message FROM webhooks WHERE user_id = ?", (user_id,))
        rows = c.fetchall()
        
    total_count = len(rows)
    
    embed = discord.Embed(title="📊 내 파트너 웹훅 연동 현황", description=f"현재 총 **{total_count}개**의 웹훅이 연동되어 있습니다.", color=0x2ecc71)
    
    if total_count == 0:
        embed.add_field(name="목록 없음", value="연동된 웹훅이 없습니다. `/웹훅설정`을 통해 추가해주세요.", inline=False)
    else:
        # 디스코드 임베드 필드 제한(최대 25개) 방지를 위해 10개까지만 상세 표시
        for i, row in enumerate(rows[:10]):
            name, interval, msg = row
            status = f"{interval}시간마다 발송" if interval > 0 else "주기 미설정 (발송 정지)"
            preview_msg = msg[:30] + "..." if len(msg) > 30 else msg
            
            embed.add_field(
                name=f"{i+1}. {name}", 
                value=f"⏱️ **상태:** {status}\n📝 **메시지:** {preview_msg}", 
                inline=False
            )
            
        if total_count > 10:
            embed.set_footer(text=f"이외에 {total_count - 10}개의 웹훅이 더 있습니다.")
            
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ----------------- 백그라운드 발송 및 웹 서버 -----------------

@tasks.loop(minutes=1)
async def webhook_sender_loop():
    current_time = time.time()
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id, webhook_url, message, interval_hours, last_sent FROM webhooks WHERE interval_hours > 0")
        rows = c.fetchall()
    
    async with aiohttp.ClientSession() as session:
        for row in rows:
            db_id, url, msg, interval_hours, last_sent = row
            interval_seconds = interval_hours * 3600 
            
            if current_time - last_sent >= interval_seconds:
                try:
                    webhook = discord.Webhook.from_url(url, session=session)
                    await webhook.send(content=msg, username="파트너 웹훅 봇")
                    
                    with sqlite3.connect(DB_NAME) as conn:
                        conn.execute("UPDATE webhooks SET last_sent = ? WHERE id = ?", (current_time, db_id))
                        conn.commit()
                    
                except discord.NotFound:
                    print(f"[웹훅 삭제됨] 연결할 수 없는 웹훅(ID: {db_id})을 DB에서 정리합니다.")
                    with sqlite3.connect(DB_NAME) as conn:
                        conn.execute("DELETE FROM webhooks WHERE id = ?", (db_id,))
                        conn.commit()
                except Exception as e:
                    print(f"[전송 실패] 웹훅 ID: {db_id} / 에러: {e}")

async def handle_health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 렌더 포트 열기 성공: 포트 {port}번에서 웹 서버 가동 중")

if __name__ == "__main__":
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        print("❌ [오류] 환경 변수에 'BOT_TOKEN'이 설정되지 않았습니다.")
    else:
        import asyncio
        async def main():
            discord.utils.setup_logging()
            await start_web_server()
            async with bot:
                await bot.start(TOKEN)

        asyncio.run(main())
