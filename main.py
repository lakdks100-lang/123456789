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
BACKUP_CHANNEL_ID = 1537622135173021756 # DB 백업용 채널 ID

def init_db():
    """DB 초기화 및 테이블 생성"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS webhooks
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER, 
                      webhook_url TEXT, 
                      server_name TEXT,
                      webhook_name TEXT,
                      message TEXT, 
                      interval_hours INTEGER, 
                      last_sent REAL)''')
        conn.commit()

async def backup_db(bot_instance):
    """지정된 백업 채널로 DB 파일을 무한 유지 백업"""
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

class UnifiedIntervalView(discord.ui.View):
    """유저 단위 메시지 주기 설정을 위한 버튼 임베드 UI"""
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id

    async def update_interval(self, interaction: discord.Interaction, hours: int):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 본인의 설정만 변경할 수 있습니다.", ephemeral=True)
            return
            
        # 해당 유저의 '모든' 웹훅 주기를 일괄 업데이트
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("UPDATE webhooks SET interval_hours = ? WHERE user_id = ?", (hours, self.user_id))
            conn.commit()
            
        await interaction.response.send_message(f"✅ **설정 완료!** 계정에 등록된 모든 웹훅의 발송 주기가 **{hours}시간**으로 통합 적용되었습니다.", ephemeral=True)

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
    
    server_name = "알 수 없는 서버"
    webhook_name = "알 수 없는 채널"

    # 웹훅 정보에서 채널명 및 서버(길드) 식별
    try:
        async with aiohttp.ClientSession() as session:
            webhook = discord.Webhook.from_url(웹훅_url, session=session)
            fetched_webhook = await webhook.fetch()
            if fetched_webhook.name:
                webhook_name = fetched_webhook.name
            # 봇이 해당 서버에 있다면 서버 이름도 가져옴
            if fetched_webhook.guild_id:
                guild = bot.get_guild(fetched_webhook.guild_id)
                if guild:
                    server_name = guild.name
    except Exception:
        pass 
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM webhooks WHERE webhook_url = ? AND user_id = ?", (웹훅_url, user_id))
        if c.fetchone():
            await interaction.response.send_message("⚠️ **이미 등록된 웹훅입니다.**", ephemeral=True)
            return

        # 해당 유저가 기존에 등록한 설정(메시지, 주기)이 있는지 확인하여 통일(상속)시킴
        c.execute("SELECT message, interval_hours FROM webhooks WHERE user_id = ? LIMIT 1", (user_id,))
        existing_setting = c.fetchone()
        
        if existing_setting:
            shared_message, shared_interval = existing_setting
        else:
            shared_message, shared_interval = "메시지가 설정되지 않았습니다.", 0

        c.execute("INSERT INTO webhooks (user_id, webhook_url, server_name, webhook_name, message, interval_hours, last_sent) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                  (user_id, 웹훅_url, server_name, webhook_name, shared_message, shared_interval, current_time))
        conn.commit()
    
    await interaction.response.send_message(f"✅ **웹훅이 추가되었습니다.**\n- 서버: `{server_name}`\n- 채널: `{webhook_name}`\n*(기존에 설정하신 메시지와 발송 주기가 이 웹훅에도 똑같이 적용됩니다.)*", ephemeral=True)
    await backup_db(bot)

@bot.tree.command(name="웹훅메시지설정", description="내 계정에 등록된 모든 웹훅의 발송 메시지를 하나로 통합 설정합니다.")
@app_commands.default_permissions(administrator=True)
async def set_unified_message(interaction: discord.Interaction, 메시지: str):
    user_id = interaction.user.id
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM webhooks WHERE user_id = ?", (user_id,))
        if not c.fetchone():
            await interaction.response.send_message("❌ **등록된 웹훅이 없습니다.** `/웹훅설정`으로 먼저 웹훅을 추가해주세요.", ephemeral=True)
            return
            
        # 해당 유저의 모든 웹훅 메시지를 일괄 업데이트
        c.execute("UPDATE webhooks SET message = ? WHERE user_id = ?", (메시지, user_id))
        conn.commit()
        
    await interaction.response.send_message("✅ **메시지 설정 완료!**\n계정에 연동된 모든 웹훅에서 해당 메시지가 발송되도록 통합 적용되었습니다.", ephemeral=True)

@bot.tree.command(name="웹훅주기", description="내 계정에 등록된 모든 웹훅의 자동 발송 주기를 버튼으로 일괄 설정합니다.")
@app_commands.default_permissions(administrator=True)
async def set_unified_interval(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM webhooks WHERE user_id = ?", (user_id,))
        if not c.fetchone():
            await interaction.response.send_message("❌ **등록된 웹훅이 없습니다.** `/웹훅설정`으로 먼저 웹훅을 추가해주세요.", ephemeral=True)
            return
        
    embed = discord.Embed(
        title="⏱️ 전체 웹훅 발송 주기 설정", 
        description="아래 버튼을 누르시면, 유저님이 등록하신 **모든 웹훅**에 메시지를 보낼 주기가 통합 변경됩니다.", 
        color=0x3498db
    )
    view = UnifiedIntervalView(user_id)
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="웹훅확인", description="현재 연동된 서버, 채널, 고유 ID 등 웹훅 상세 정보를 확인합니다.")
@app_commands.default_permissions(administrator=True)
async def check_webhooks(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        # id(고유 ID), 서버명, 채널명, 주기, 메시지 순으로 가져옴
        c.execute("SELECT id, server_name, webhook_name, interval_hours, message FROM webhooks WHERE user_id = ?", (user_id,))
        rows = c.fetchall()
        
    total_count = len(rows)
    
    if total_count == 0:
        await interaction.response.send_message("❌ 연동된 웹훅이 없습니다. `/웹훅설정`을 통해 추가해주세요.", ephemeral=True)
        return

    # 공통 설정값 추출 (어차피 동일함)
    common_interval = rows[0][3]
    common_msg = rows[0][4]
    status = f"**{common_interval}시간**마다 발송" if common_interval > 0 else "주기 미설정 (발송 정지)"
    preview_msg = common_msg[:40] + "..." if len(common_msg) > 40 else common_msg

    embed = discord.Embed(title="📊 내 파트너 웹훅 연동 현황", color=0x2ecc71)
    embed.description = f"**[공통 설정]**\n⏱️ **주기:** {status}\n📝 **메시지:** {preview_msg}\n\n**[등록된 웹훅 목록 - 총 {total_count}개]**"
    
    # 25개 임베드 필드 제한 방지
    for row in rows[:15]:
        hook_id, s_name, w_name, _, _ = row
        embed.add_field(
            name=f"🔑 고유 ID: {hook_id}", 
            value=f"🏢 **서버:** {s_name}\n💬 **채널:** {w_name}", 
            inline=False
        )
        
    if total_count > 15:
        embed.set_footer(text=f"이외에 {total_count - 15}개의 웹훅이 더 있습니다. 삭제 시 고유 ID를 이용해주세요.")
            
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="웹훅삭제", description="고유 ID를 입력하여 특정 웹훅만 삭제합니다.")
@app_commands.default_permissions(administrator=True)
async def delete_webhook(interaction: discord.Interaction, 고유_id: int):
    user_id = interaction.user.id
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT server_name, webhook_name FROM webhooks WHERE id = ? AND user_id = ?", (고유_id, user_id))
        target = c.fetchone()
        
        if not target:
            await interaction.response.send_message(f"❌ **고유 ID {고유_id}번** 웹훅을 찾을 수 없거나 삭제 권한이 없습니다.", ephemeral=True)
            return
            
        s_name, w_name = target
        c.execute("DELETE FROM webhooks WHERE id = ?", (고유_id,))
        conn.commit()
        
    await interaction.response.send_message(f"🗑️ **삭제 완료:** 고유 ID **{고유_id}**번 웹훅이 제거되었습니다.\n(서버: `{s_name}` / 채널: `{w_name}`)", ephemeral=True)

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
