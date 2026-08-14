import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import time
import aiohttp
import os
import re

# 봇 기본 설정
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

DB_NAME = 'partner_bot.db'
BACKUP_CHANNEL_ID = 1537622135173021756

def init_db():
    """DB 초기화 및 테이블 생성 (안전한 연결 방식)"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS webhooks
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER, 
                      webhook_url TEXT, 
                      message TEXT, 
                      interval_hours INTEGER, 
                      last_sent REAL)''')
        conn.commit()

async def backup_db(bot_instance):
    """지정된 백업 채널로 DB 파일을 타임스탬프와 함께 다운로드 형태로 전송"""
    channel = bot_instance.get_channel(BACKUP_CHANNEL_ID)
    if channel:
        try:
            # 파일명에 현재 시간을 넣어 덮어씌워지지 않고 무한 유지되도록 설정
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
    """메시지 주기 설정을 위한 인터랙티브 버튼 UI"""
    def __init__(self, user_id: int, webhook_url: str):
        super().__init__(timeout=120) # 타임아웃 2분으로 넉넉하게 연장
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
            
        await interaction.response.send_message(f"✅ 주기가 **{hours}시간** 단위로 정밀하게 설정되었습니다.\n이제 지정된 시간마다 봇이 자동으로 메시지를 발송합니다.", ephemeral=True)

    @discord.ui.button(label="1시간", style=discord.ButtonStyle.primary, custom_id="btn_1h")
    async def btn_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_interval(interaction, 1)
        
    @discord.ui.button(label="6시간", style=discord.ButtonStyle.primary, custom_id="btn_6h")
    async def btn_6(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_interval(interaction, 6)
        
    @discord.ui.button(label="12시간", style=discord.ButtonStyle.primary, custom_id="btn_12h")
    async def btn_12(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_interval(interaction, 12)
        
    @discord.ui.button(label="24시간", style=discord.ButtonStyle.success, custom_id="btn_24h")
    async def btn_24(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_interval(interaction, 24)

@bot.event
async def on_ready():
    init_db() # 봇 켜질 때 DB 구조 확인
    print(f"✅ 봇 로그인 완료: {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ 슬래시 명령어 {len(synced)}개 전역 동기화 완료.")
    except Exception as e:
        print(f"❌ 동기화 에러: {e}")
        
    if not webhook_sender_loop.is_running():
        webhook_sender_loop.start()

@bot.tree.command(name="웹훅지정", description="새로운 파트너 웹훅 URL과 보낼 메시지를 등록합니다.")
@app_commands.default_permissions(administrator=True)
async def set_webhook(interaction: discord.Interaction, 웹훅_url: str, 메시지: str):
    # 디스코드 웹훅 URL 형식이 맞는지 정규식으로 1차 검증
    if not re.match(r"^https://(?:ptb\.|canary\.)?discord\.com/api/webhooks/\d+/[a-zA-Z0-9_-]+$", 웹훅_url):
        await interaction.response.send_message("❌ **올바른 디스코드 웹훅 URL이 아닙니다.** 다시 확인해주세요.", ephemeral=True)
        return

    user_id = interaction.user.id
    current_time = time.time()
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO webhooks (user_id, webhook_url, message, interval_hours, last_sent) VALUES (?, ?, ?, ?, ?)", 
                  (user_id, 웹훅_url, 메시지, 0, current_time))
        conn.commit()
    
    await interaction.response.send_message("✅ **웹훅이 데이터베이스에 등록되었습니다.**\n바로 `/메시지주기설정` 명령어를 사용하여 전송 주기를 활성화해주세요.", ephemeral=True)
    
    # DB 저장 직후 백업 채널로 전송
    await backup_db(bot)

@bot.tree.command(name="메시지주기설정", description="등록한 웹훅의 자동 발송 주기를 버튼으로 설정합니다.")
@app_commands.default_permissions(administrator=True)
async def set_interval(interaction: discord.Interaction, 웹훅_url: str):
    user_id = interaction.user.id
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM webhooks WHERE webhook_url = ? AND user_id = ?", (웹훅_url, user_id))
        result = c.fetchone()
    
    if not result:
        await interaction.response.send_message("❌ **DB에서 해당 웹훅을 찾을 수 없거나 소유 권한이 없습니다.** URL을 확인해주세요.", ephemeral=True)
        return
        
    view = IntervalView(user_id, 웹훅_url)
    await interaction.response.send_message("⏱️ **이 웹훅에 대해 자동 메시지를 발송할 주기를 선택해주세요:**", view=view, ephemeral=True)

@tasks.loop(minutes=1)
async def webhook_sender_loop():
    """1분마다 실행되며 조건이 충족된 웹훅으로 메시지를 발송합니다."""
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
                    
                    # 성공 시 last_sent 갱신
                    with sqlite3.connect(DB_NAME) as conn:
                        conn.execute("UPDATE webhooks SET last_sent = ? WHERE id = ?", (current_time, db_id))
                        conn.commit()
                    print(f"[전송 성공] 웹훅 ID: {db_id}")
                    
                except discord.NotFound:
                    # 404 에러: 웹훅이 삭제된 경우 DB에서 자동 삭제 처리 (가비지 컬렉션)
                    print(f"[웹훅 삭제됨] 연결할 수 없는 웹훅(ID: {db_id})을 DB에서 정리합니다.")
                    with sqlite3.connect(DB_NAME) as conn:
                        conn.execute("DELETE FROM webhooks WHERE id = ?", (db_id,))
                        conn.commit()
                        
                except Exception as e:
                    print(f"[전송 실패] 웹훅 ID: {db_id} / 에러: {e}")

# Render 환경 변수(Environment Variables)에서 토큰을 가져와 실행합니다.
if __name__ == "__main__":
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        print("❌ [오류] 환경 변수에 'BOT_TOKEN'이 설정되지 않았습니다.")
    else:
        bot.run(TOKEN)
