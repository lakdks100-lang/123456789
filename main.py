import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import time
import aiohttp
import os

# 봇 기본 설정
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# DB 초기화 및 테이블 생성
conn = sqlite3.connect('partner_bot.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS webhooks
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER, 
              webhook_url TEXT, 
              message TEXT, 
              interval_hours INTEGER, 
              last_sent REAL)''')
conn.commit()

# 백업 채널 ID
BACKUP_CHANNEL_ID = 1537622135173021756

async def backup_db(bot_instance):
    """지정된 채널로 DB 파일을 다운로드 형태로 백업"""
    channel = bot_instance.get_channel(BACKUP_CHANNEL_ID)
    if channel:
        try:
            # DB 파일이 안전하게 기록되도록 대기
            conn.commit()
            await channel.send("💾 **새로운 웹훅이 등록되어 DB가 백업되었습니다.**", file=discord.File("partner_bot.db"))
        except Exception as e:
            print(f"백업 실패: {e}")

class IntervalView(discord.ui.View):
    """메시지 주기 설정을 위한 버튼 UI"""
    def __init__(self, user_id: int, webhook_url: str):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.webhook_url = webhook_url

    async def update_interval(self, interaction: discord.Interaction, hours: int):
        # 명령어를 친 본인만 버튼을 조작할 수 있도록 제한
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("본인이 등록한 웹훅의 주기만 설정할 수 있습니다.", ephemeral=True)
            return
            
        # DB에 주기(시간) 업데이트
        c.execute("UPDATE webhooks SET interval_hours = ? WHERE webhook_url = ? AND user_id = ?", 
                  (hours, self.webhook_url, self.user_id))
        conn.commit()
        await interaction.response.send_message(f"✅ 주기가 **{hours}시간** 단위로 정밀하게 설정되었습니다.", ephemeral=True)

    @discord.ui.button(label="1시간", style=discord.ButtonStyle.primary)
    async def btn_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_interval(interaction, 1)
        
    @discord.ui.button(label="6시간", style=discord.ButtonStyle.primary)
    async def btn_6(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_interval(interaction, 6)
        
    @discord.ui.button(label="12시간", style=discord.ButtonStyle.primary)
    async def btn_12(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_interval(interaction, 12)
        
    @discord.ui.button(label="24시간", style=discord.ButtonStyle.success)
    async def btn_24(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_interval(interaction, 24)

@bot.event
async def on_ready():
    print(f"봇 로그인 완료: {bot.user.name}")
    try:
        # 슬래시 명령어 동기화
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료.")
    except Exception as e:
        print(f"동기화 에러: {e}")
        
    # 자동 발송 루프 시작 (1분마다 체크하여 정밀하게 발송)
    if not webhook_sender_loop.is_running():
        webhook_sender_loop.start()

@bot.tree.command(name="웹훅지정", description="새로운 웹훅 URL과 보낼 메시지를 등록합니다.")
@app_commands.default_permissions(administrator=True) # 관리자 이상만 사용 가능
async def set_webhook(interaction: discord.Interaction, 웹훅_url: str, 메시지: str):
    user_id = interaction.user.id
    current_time = time.time()
    
    # DB에 웹훅 정보 저장 (초기 주기는 0으로 설정되어 발송되지 않음)
    c.execute("INSERT INTO webhooks (user_id, webhook_url, message, interval_hours, last_sent) VALUES (?, ?, ?, ?, ?)", 
              (user_id, 웹훅_url, 메시지, 0, current_time))
    conn.commit()
    
    await interaction.response.send_message("✅ **웹훅이 성공적으로 등록되었습니다.**\n바로 `/메시지주기설정` 명령어를 사용해 전송 주기를 정해주세요.", ephemeral=True)
    
    # 백업 채널로 DB 전송
    await backup_db(bot)

@bot.tree.command(name="메시지주기설정", description="등록한 웹훅의 자동 발송 주기를 버튼으로 설정합니다.")
@app_commands.default_permissions(administrator=True) # 관리자 이상만 사용 가능
async def set_interval(interaction: discord.Interaction, 웹훅_url: str):
    user_id = interaction.user.id
    
    # 본인이 등록한 웹훅이 맞는지 검증
    c.execute("SELECT id FROM webhooks WHERE webhook_url = ? AND user_id = ?", (웹훅_url, user_id))
    result = c.fetchone()
    
    if not result:
        await interaction.response.send_message("❌ **등록하신 웹훅을 찾을 수 없거나 권한이 없습니다.** URL을 다시 확인해주세요.", ephemeral=True)
        return
        
    view = IntervalView(user_id, 웹훅_url)
    await interaction.response.send_message("⏱️ **이 웹훅에 대해 메시지를 보낼 주기를 선택해주세요:**", view=view, ephemeral=True)

@tasks.loop(minutes=1)
async def webhook_sender_loop():
    """1분마다 실행되며 시간이 다 된 웹훅에 메시지를 정밀하게 쏩니다."""
    current_time = time.time()
    
    # 주기가 설정된(0보다 큰) 웹훅만 불러오기
    c.execute("SELECT id, webhook_url, message, interval_hours, last_sent FROM webhooks WHERE interval_hours > 0")
    rows = c.fetchall()
    
    async with aiohttp.ClientSession() as session:
        for row in rows:
            db_id, url, msg, interval_hours, last_sent = row
            interval_seconds = interval_hours * 3600  # 시간을 초 단위로 변환
            
            # 마지막으로 보낸 시간 + 주기보다 현재 시간이 크거나 같으면 전송
            if current_time - last_sent >= interval_seconds:
                try:
                    webhook = discord.Webhook.from_url(url, session=session)
                    await webhook.send(content=msg, username="파트너 봇")
                    
                    # 성공적으로 보냈다면 마지막 전송 시간을 현재로 업데이트
                    c.execute("UPDATE webhooks SET last_sent = ? WHERE id = ?", (current_time, db_id))
                    conn.commit()
                    print(f"[성공] 웹훅 전송 완료: {url}")
                except Exception as e:
                    print(f"[실패] 웹훅 전송 에러 ({url}): {e}")

# 봇 토큰을 입력하고 실행하세요.
bot.run("여기에_봇_토큰을_입력하세요")
