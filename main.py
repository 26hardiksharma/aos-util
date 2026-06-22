import os
import asyncio
import discord
import boto3
import io
import contextlib
import textwrap
from dotenv import load_dotenv
from discord.ext import commands
from mcstatus import JavaServer
import pytz
from discord.ext import tasks
from datetime import datetime, timezone
import pytz
SERVER_START_TIME = None

load_dotenv()


INSTANCE_ID = os.getenv("INSTANCE_ID")

AUTHORIZED_USERS = [
    587584984097751040,
    789495762325078078,
    602330585654099969,
    657618021480660993
]

MC_BOT_ID = 1082588508591501343
MC_COMMAND_CHANNEL = 1197985694128296029
CONTROL_PANEL_CHANNEL = 1518599666734727315
LOG_CHANNEL = 1518630213183869008


idle_ticks = 0
MAX_IDLE_TICKS = 3

intents = discord.Intents.all()
intents.presences = True
intents.members = True
intents.message_content = True

client = commands.Bot(
    command_prefix="<@1518569496103616593> ",
    intents=intents,
    case_insensitive=True,
    strip_after_prefix=True
)



ec2 = boto3.client(
    "ec2",
    region_name="ap-south-1",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("AWS_SECRET_KEY")
)

ssm = boto3.client(
    "ssm",
    region_name="ap-south-1",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("AWS_SECRET_KEY")
)

def get_instance_status():
    response = ec2.describe_instances(InstanceIds=[INSTANCE_ID])

    return (
        response["Reservations"][0]
        ["Instances"][0]
        ["State"]["Name"]
    )


panel_message = None


def get_minecraft_info():

    try:
        server = JavaServer.lookup("13.205.205.48:31121")
        status = server.status()

        return {
            "online": True,
            "players": status.players.online,
            "max_players": status.players.max,
            "latency": round(status.latency)
        }

    except Exception:
        return {
            "online": False,
            "players": 0,
            "max_players": 0,
            "latency": 0
        }

def create_status_embed(guild):

    mc_info = get_minecraft_info()

    if mc_info["online"]:
        player_text = (
            f"🟢 {mc_info['players']}/{mc_info['max_players']} players"
        )

    else:
        player_text = "🔴 Offline"


    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)

    # ---------------- EC2 STATUS ----------------
    instance = ec2.describe_instances(
        InstanceIds=[INSTANCE_ID]
    )["Reservations"][0]["Instances"][0]

    ec2_state = instance["State"]["Name"]
    launch_time = instance.get("LaunchTime")

    if launch_time and ec2_state == "running":
        launch_time = launch_time.astimezone(ist)
        delta = now - launch_time

        days = delta.days
        hours, rem = divmod(delta.seconds, 3600)
        minutes, sec = divmod(rem, 60)
        uptime = (
            f"{days}d {hours}h {minutes} {sec}sm"
            if days
            else f"{hours}h {minutes}m {sec}s"
        )
    else:
        uptime = "N/A"

    ec2_status = (
        "🟢 Running" if ec2_state == "running"
        else "🟡 Pending" if ec2_state in ["pending", "stopping"]
        else "🔴 Stopped"
    )

    # ---------------- MINECRAFT STATUS ----------------
    mc = guild.get_member(MC_BOT_ID)

    mc_status = (
        "🟢 Online"
        if mc and str(mc.status) == "online"
        else "🔴 Offline"
    )

    # ---------------- EMBED ----------------
    embed = discord.Embed(
        title = "Minecraft Server Control Panel",
        description=(
            f"🖥️ **EC2 Instance**\n"
            f"└─ {ec2_status}\n\n"
            f"⛏️ **Minecraft Server**\n"
            f"└─ {mc_status}\n\n"
            f"👥 Players\n"
            f"└─ {player_text}\n\n"
            f"⏱️ Uptime\n"
            f"└─ 🟢 {uptime}"
        ),
        color=discord.Color.green()
        if ec2_state == "running"
        else discord.Color.red(),
        
    )
    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1020949894745296896/1518610514563829871/ouTmySN.png?ex=6a3a8bc1&is=6a393a41&hm=8da330acc56ad99f708139b93f4e62dbe9d075bbc6b5f31ffc574d39f4ee9f41&")
    # embed.add_field(
    #     name="👥 Players",
    #     value=player_text,
    #     inline=True
    # )
    # embed.add_field(
    #     name="⏱️ Uptime",
    #     value = f"{uptime}"
    #     inline=False
    # )
    embed.set_footer(text=f"Last Updated: {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    return embed


async def refresh_panel(interaction, view):

    channel = interaction.channel

    try:
        await interaction.message.edit(
            embed=create_status_embed(interaction.guild),
            view=view
        )
    except:
        return None
async def start_minecraft_server(guild):

    mc = guild.get_member(MC_BOT_ID)

    if mc and str(mc.status) == "online":
        return "already_running"

    instance_active = get_instance_status() == "running"

    if not instance_active:
        ec2.start_instances(
            InstanceIds=[INSTANCE_ID]
        )

        await asyncio.sleep(30)

    ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": [
                "cd /home/ubuntu/minecraft && ./start.sh"
            ]
        }
    )

    if not mc:
        return "started"

    try:

        def check(before, after):
            return after.id == mc.id

        await client.wait_for(
            "presence_update",
            check=check,
            timeout=100
        )

        return "online"

    except asyncio.TimeoutError:
        return "timeout"


async def stop_minecraft_server(guild):

    if get_instance_status() != "running":
        return "already_off"

    mc = guild.get_member(MC_BOT_ID)

    if mc and str(mc.status) == "online":

        channel = guild.get_channel(MC_COMMAND_CHANNEL)

        if channel:
            await channel.send("stop")

        await asyncio.sleep(120)

    ec2.stop_instances(InstanceIds=[INSTANCE_ID])

    return "stopped"


class ControlView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    # ======================================================
    # START BUTTON
    # ======================================================
    @discord.ui.button(
        label="🟢 Start Server",
        style=discord.ButtonStyle.green,
        custom_id="aws_start"
    )
    async def start_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ Administrator permissions required.",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        try:
            result = await start_minecraft_server(interaction.guild)

            # refresh dashboard FIRST
            await refresh_panel(interaction, self)

            if result == "already_running":
                await interaction.followup.send(
                    "✅ Server is already running.",
                    ephemeral=True
                )

            elif result == "online":
                await interaction.followup.send(
                    "🚀 Server started successfully.",
                    ephemeral=True
                )

            elif result == "timeout":
                await interaction.followup.send(
                    "⚠️ Server started but status not confirmed.",
                    ephemeral=True
                )

            else:
                await interaction.followup.send(
                    "🚀 Start request sent.",
                    ephemeral=True
                )

        except Exception as e:
            await refresh_panel(interaction, self)

            await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True
            )

    # ======================================================
    # STOP BUTTON
    # ======================================================
    @discord.ui.button(
        label="🔴 Stop Server",
        style=discord.ButtonStyle.red,
        custom_id="aws_stop"
    )
    async def stop_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ Administrator permissions required.",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        try:
            result = await stop_minecraft_server(interaction.guild)

            # refresh dashboard FIRST
            await refresh_panel(interaction, self)

            if result == "already_off":
                await interaction.followup.send(
                    "⚠️ Server is already stopped.",
                    ephemeral=True
                )

            else:
                await interaction.followup.send(
                    "🛑 Server stopped successfully.",
                    ephemeral=True
                )

        except Exception as e:
            await refresh_panel(interaction, self)

            await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True
            )

    # ======================================================
    # REFRESH BUTTON
    # ======================================================
    @discord.ui.button(
        label="🔄 Refresh",
        style=discord.ButtonStyle.blurple,
        custom_id="aws_refresh"
    )
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await refresh_panel(interaction, self)

        await interaction.response.send_message(
            "🔄 Updated dashboard.",
            ephemeral=True
        )

control_view = None
@client.command(name="start")
@commands.cooldown(
    1,
    60.0,
    commands.BucketType.guild
)
async def start_server(ctx):

    if ctx.author.id not in AUTHORIZED_USERS:
        ctx.command.reset_cooldown(ctx)

        return await ctx.send("Not authorized.")

    result = await start_minecraft_server(
        ctx.guild
    )

    await ctx.send(f"Result: {result}")


@client.command(name="stop")
async def stop_server(ctx):

    if ctx.author.id not in AUTHORIZED_USERS:
        return await ctx.send("Not authorized.")

    result = await stop_minecraft_server(
        ctx.guild
    )

    await ctx.send(f"Result: {result}")


@client.command(name="status")
async def status(ctx):
    await ctx.send(
        get_instance_status()
    )


@client.command()
async def setup(ctx):
    global panel_message
    global control_view

    if control_view is None:
        control_view = ControlView()

    channel = ctx.guild.get_channel(
        CONTROL_PANEL_CHANNEL
    )

    panel_message = await channel.send(
        embed=create_status_embed(ctx.guild),
        view=control_view
    )


@client.event
async def on_ready():
    global control_view

    if control_view is None:
        control_view = ControlView()
    client.add_view(control_view)
    
    if not refresh_dashboard.is_running():
        refresh_dashboard.start()

    global panel_message

    try:
        channel = await client.fetch_channel(
            CONTROL_PANEL_CHANNEL
        )

        async for msg in channel.history(limit=10):
            if msg.author.id == client.user.id and msg.embeds:
                panel_message = msg
                break

    except Exception:
        pass
    log_channel = await client.fetch_channel(LOG_CHANNEL)
    await log_channel.send(f"Logged in as {client.user}")
def clean_code(content):
  if content.startswith("```") and content.endswith("```"):
    return "\n".join(content.split("\n")[1:])[:-3]
  else:
    return content
@client.command(aliases = ['eval'])
async def evaluate(ctx, *, arg = None):
  if not ctx.author.id in [602330585654099969,587584984097751040,789495762325078078]:
    return
  if arg == None:
    await ctx.send('I Got Nothing To Evaluate, Bro!')
    return
  if "token" in arg.lower():
    return await ctx.send('My Token Is Damn Secret And Cannot Be Leaked.')
  code = clean_code(arg)
  local_variables = {
    "discord": discord,
    "commands": commands,
    "client": client,
    "ctx": ctx,
    "channel": ctx.channel,
    "author": ctx.author,
    "guild": ctx.guild,
    "message": ctx.message,
    }

  stdout = io.StringIO()
  try:
    with contextlib.redirect_stdout(stdout):
      exec(f"async def func():\n{textwrap.indent(code, '    ')}", local_variables,)
      obj = await local_variables["func"]()
      result = f"{stdout.getvalue()}"
  except Exception as e:
    kekek = f"{e}, {e}, {e.__traceback__}"
    result = "".join(kekek)
  embed = discord.Embed(title = "Eval",color = ctx.author.color)
  embed.add_field(name = "Command",value = f"{arg}")
  embed.add_field(name = "Result",value = result,inline= False)
  await ctx.send(embed = embed)

@tasks.loop(minutes = 1)
async def refresh_dashboard():

    global panel_message
    global control_view,idle_ticks


    if not panel_message or not control_view:
        return
    

    try:
        guild = panel_message.guild

        await panel_message.edit(
            embed=create_status_embed(guild),
            view=control_view
        )

        print("Success!")

        mc_info = get_minecraft_info()

        if mc_info["online"] and mc_info["players"] == 0:
            idle_ticks += 1
            print(f"Idle ticks: {idle_ticks}")
        
        else:
            idle_ticks = 0  # reset if players join or server offline

        if idle_ticks >= MAX_IDLE_TICKS:
            log_channel = panel_message.guild.get_channel(LOG_CHANNEL)
            await log_channel.send("No players detected. Shutting down server")

            # reset counter so it doesn't spam shutdown
            idle_ticks = 0

            # shutdown EC2

            await stop_minecraft_server(panel_message.guild)

    except Exception as e:
        print("Refresh error:", repr(e))


client.run(os.getenv("TOKEN"))
