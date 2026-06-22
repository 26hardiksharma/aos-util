import os
import asyncio
import discord
import boto3
import io
import contextlib
import textwrap
from dotenv import load_dotenv
from discord.ext import commands

load_dotenv()

# ==========================================================
# CONFIG
# ==========================================================

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
LOG_CHANNEL = 1020949894745296896

# ==========================================================
# DISCORD SETUP
# ==========================================================

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

# ==========================================================
# AWS CLIENTS
# ==========================================================

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

# ==========================================================
# HELPERS
# ==========================================================

def get_instance_status():
    response = ec2.describe_instances(InstanceIds=[INSTANCE_ID])

    return (
        response["Reservations"][0]
        ["Instances"][0]
        ["State"]["Name"]
    )


def create_status_embed(guild):

    mc = guild.get_member(MC_BOT_ID)

    ec2_status = get_instance_status()

    mc_status = (
        "🟢 Online"
        if mc and str(mc.status) == "online"
        else "🔴 Offline"
    )

    embed = discord.Embed(
        title="Server Management Panel",
        description="Manage the Minecraft server.",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="EC2 Instance",
        value=f"`{ec2_status}`",
        inline=False
    )

    embed.add_field(
        name="Minecraft Server",
        value=mc_status,
        inline=False
    )

    return embed


async def refresh_panel(interaction, view):
    await interaction.message.edit(
        embed=create_status_embed(interaction.guild),
        view=view
    )


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


# ==========================================================
# BUTTON VIEW
# ==========================================================

class ControlView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Start Server",
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

            result = await start_minecraft_server(
                interaction.guild
            )

            await refresh_panel(interaction, self)

            if result == "already_running":
                await interaction.followup.send(
                    "✅ Minecraft server is already running.",
                    ephemeral=True
                )

            elif result == "online":
                await interaction.followup.send(
                    "🚀 Minecraft server is online.",
                    ephemeral=True
                )

            elif result == "timeout":
                await interaction.followup.send(
                    "⚠️ Startup command sent but online status was not detected.",
                    ephemeral=True
                )

            else:
                await interaction.followup.send(
                    "🚀 Startup request sent.",
                    ephemeral=True
                )

        except Exception as e:

            await refresh_panel(interaction, self)

            await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True
            )

    @discord.ui.button(
        label="Stop Server",
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

            result = await stop_minecraft_server(
                interaction.guild
            )

            await refresh_panel(interaction, self)

            if result == "already_off":
                await interaction.followup.send(
                    "⚠️ Instance is already stopped.",
                    ephemeral=True
                )

            else:
                await interaction.followup.send(
                    "🛑 Minecraft server and EC2 instance stopped.",
                    ephemeral=True
                )

        except Exception as e:

            await refresh_panel(interaction, self)

            await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True
            )

    @discord.ui.button(
        label="Refresh Status",
        style=discord.ButtonStyle.blurple,
        custom_id="aws_status"
    )
    async def status_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await refresh_panel(interaction, self)

        await interaction.response.send_message(
            "🔄 Status refreshed.",
            ephemeral=True
        )


# ==========================================================
# COMMANDS
# ==========================================================

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

    channel = ctx.guild.get_channel(
        CONTROL_PANEL_CHANNEL
    )

    await channel.send(
        embed=create_status_embed(ctx.guild),
        view=ControlView()
    )


# ==========================================================
# EVENTS
# ==========================================================

@client.event
async def on_ready():

    client.add_view(ControlView())

    try:
        channel = await client.fetch_channel(
            LOG_CHANNEL
        )

        await channel.send(
            "Logged in."
        )

    except Exception:
        pass

    print(f"Logged in as {client.user}")

def clean_code(content):
  if content.startswith("```") and content.endswith("```"):
    return "\n".join(content.split("\n")[1:])[:-3]
  else:
    return content
@client.command(aliases = ['eval'])
async def evaluate(ctx, *, arg = None):
  if not ctx.author.id == 920564227570270208:
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
client.run(os.getenv("TOKEN"))
