import os
import asyncio
import json
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

CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "config.json"
)


def validate_config(config):
    required_keys = {
        "autostop": {"enabled", "max_idle_ticks"},
        "minecraft": {
            "bot_id",
            "chat_channel",
            "command_channel",
            "server_address"
        },
        "discord": {"control_panel_channel", "log_channel"},
        "permissions": {"authorized_users"},
        "timeouts": {"startup_wait_seconds", "ssm_wait_seconds"}
    }

    if not isinstance(config, dict):
        raise ValueError("config.json must contain a JSON object.")

    for section, keys in required_keys.items():
        if section not in config:
            raise ValueError(f"Missing required configuration section: {section}")
        if not isinstance(config[section], dict):
            raise ValueError(f"Configuration section '{section}' must be an object.")

        missing_keys = keys - config[section].keys()
        if missing_keys:
            missing = ", ".join(sorted(missing_keys))
            raise ValueError(
                f"Missing required key(s) in configuration section "
                f"'{section}': {missing}"
            )

    if not isinstance(config["autostop"]["enabled"], bool):
        raise ValueError("'autostop.enabled' must be a boolean.")
    if not isinstance(config["autostop"]["max_idle_ticks"], int):
        raise ValueError("'autostop.max_idle_ticks' must be an integer.")
    if not isinstance(config["minecraft"]["server_address"], str):
        raise ValueError("'minecraft.server_address' must be a string.")
    if not isinstance(config["permissions"]["authorized_users"], list):
        raise ValueError("'permissions.authorized_users' must be a list.")


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            config = json.load(file)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Configuration file not found: {CONFIG_FILE}"
        ) from error
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in config.json at line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        ) from error
    except OSError as error:
        raise OSError(f"Unable to read config.json: {error}") from error

    validate_config(config)
    return config


def save_config(config):
    validate_config(config)
    temporary_file = f"{CONFIG_FILE}.tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=4)
        file.write("\n")
    os.replace(temporary_file, CONFIG_FILE)


try:
    CONFIG = load_config()
except Exception as error:
    print(f"❌ Configuration Error: {error}")
    raise

INSTANCE_ID = os.getenv("INSTANCE_ID")


idle_ticks = 0
DASHBOARD_REFRESH_MINUTES = 5
starting_server = False
stopping_server = False

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


async def log_exception(title, error):
    message = f"❌ {title}:\n{type(error).__name__}: {error}"

    try:
        log_channel = getattr(client, "log_channel", None)
        if log_channel is None:
            log_channel = await client.fetch_channel(
                CONFIG["discord"]["log_channel"]
            )
        await log_channel.send(message)
    except Exception as logging_error:
        print(f"Failed to log exception: {logging_error!r}")


async def wait_for_ssm(instance_id, timeout=60):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    ssm.describe_instance_information
                ),
                timeout=remaining
            )
        except asyncio.TimeoutError:
            return False

        if any(
            instance.get("InstanceId") == instance_id
            and instance.get("PingStatus") == "Online"
            for instance in response.get("InstanceInformationList", [])
        ):
            return True

        remaining = deadline - loop.time()
        if remaining <= 0:
            return False

        await asyncio.sleep(min(5, remaining))


panel_message = None


def get_minecraft_info():

    try:
        server = JavaServer.lookup(
            CONFIG["minecraft"]["server_address"]
        )
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
        ping_text = f"🟢 {mc_info['latency']} ms"

    else:
        player_text = "🔴 Offline"
        ping_text = "🔴 N/A"


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
            f"🟢 {days}d {hours}h {minutes}m {sec}s"
            if days
            else f"🟢 {hours}h {minutes}m {sec}s"
        )
    else:
        uptime = "🔴 N/A"

    ec2_status = (
        "🟢 Running" if ec2_state == "running"
        else "🟡 Pending" if ec2_state in ["pending", "stopping"]
        else "🔴 Stopped"
    )

    # ---------------- MINECRAFT STATUS ----------------
    mc = guild.get_member(CONFIG["minecraft"]["bot_id"])

    mc_status = (
        "🟢 Online"
        if mc and str(mc.status) == "online"
        else "🔴 Offline"
    )
    autostop_enabled = CONFIG["autostop"]["enabled"]
    autostop_text = "🟢 Active" if autostop_enabled else "🔴 Inactive"
    if autostop_enabled:
        autostop_duration = (
            CONFIG["autostop"]["max_idle_ticks"]
            * DASHBOARD_REFRESH_MINUTES
        )
        autostop_text = f"🟢 Active ({autostop_duration} min)"
    # ---------------- EMBED ----------------
    embed = discord.Embed(
        title = "Minecraft Server Control Panel",
        # description=(
        #     f"🖥️ **EC2 Instance**\n"
        #     f"└─ {ec2_status}\n\n"
        #     f"⛏️ **Minecraft Server**\n"
        #     f"└─ {mc_status}\n\n"
        #     f"👥 Players\n"
        #     f"└─ {player_text}\n\n"
        #     f"⏱️ Uptime\n"
        #     f"└─ {uptime}\n\n"
        #     f"🔀 Auto-Stop\n"
        #     f"└─ {CONFIG['autostop']['enabled']}"
        # ),
        color=(
            discord.Color.green()
            if ec2_state == "running"
            else discord.Color.yellow()
            if ec2_state in ["pending", "stopping"]
            else discord.Color.red()
        ),
            
    )

    embed.add_field(
            name = f"🖥️ **EC2 Instance**",
            value = f"└─ {ec2_status}",
            inline = True
    )

    embed.add_field(
        name = "⛏️ **Minecraft Server**",
        value = f"└─ {mc_status}",
        inline = True
    )

    embed.add_field(name=f"📡 Ping", value=f"└─ {ping_text}", inline=True)

    embed.add_field(
        name = "👥 Players",
        value = f"└─ {player_text}",
        inline = True
    )

    embed.add_field(
        name = "⏱️ Uptime",
        value = f"└─ {uptime}",
        inline = True
    )

    embed.add_field(
        name = "🔀 Auto-Stop",
        value = f"└─ {autostop_text}",
        inline = True
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


async def update_operation_buttons():
    if control_view is None:
        return

    operations_running = starting_server or stopping_server
    control_view.set_operation_buttons_disabled(operations_running)

    if panel_message:
        try:
            await panel_message.edit(view=control_view)
        except (discord.HTTPException, discord.NotFound):
            pass

async def start_minecraft_server(guild):
    global starting_server

    if starting_server:
        return "already_starting"
    if stopping_server:
        return "already_stopping"

    starting_server = True

    try:
        await update_operation_buttons()
        log_channel = await client.fetch_channel(
            CONFIG["discord"]["log_channel"]
        )
        mc = guild.get_member(CONFIG["minecraft"]["bot_id"])

        if mc and str(mc.status) == "online":
            return "already_running"

        await log_channel.send("🚀 Server start initiated.")
        instance_active = get_instance_status() == "running"

        if not instance_active:
            ec2.start_instances(
                InstanceIds=[INSTANCE_ID]
            )

            waiter = ec2.get_waiter("instance_running")
            startup_timeout = CONFIG["timeouts"]["startup_wait_seconds"]
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        waiter.wait,
                        InstanceIds=[INSTANCE_ID]
                    ),
                    timeout=startup_timeout
                )
            except asyncio.TimeoutError:
                await log_channel.send(
                    f"❌ AWS startup timed out after {startup_timeout} seconds "
                    "while waiting "
                    "for the instance to reach the running state."
                )
                return "aws_timeout"

            await log_channel.send("✅ AWS instance reached running state.")

        ssm_timeout = CONFIG["timeouts"]["ssm_wait_seconds"]
        await log_channel.send("⏳ Waiting for SSM agent...")
        ssm_ready = await wait_for_ssm(
            INSTANCE_ID,
            timeout=ssm_timeout
        )
        if not ssm_ready:
            await log_channel.send(
                "❌ SSM agent did not become ready within timeout."
            )
            return "ssm_timeout"

        await log_channel.send("✅ SSM agent is online.")

        try:
            ssm.send_command(
                InstanceIds=[INSTANCE_ID],
                DocumentName="AWS-RunShellScript",
                Parameters={
                    "commands": [
                        "cd /home/ubuntu/minecraft && ./start.sh"
                    ]
                }
            )
        except Exception as e:
            await log_channel.send(
                f"❌ SSM SendCommand Error:\n{e}"
            )
            raise

        if not mc:
            return "started"

        try:
            def check(msg):
                return msg.channel.id == CONFIG["minecraft"]["chat_channel"] and msg.author.id == CONFIG["minecraft"]["bot_id"] and "server has started" in msg.content.lower()

            await client.wait_for(
                "message",
                check=check,
                timeout=100
            )
            await log_channel.send("✅ Minecraft startup confirmed.")
            return "online"

        except asyncio.TimeoutError:
            await log_channel.send("The server either failed to start in 100 seconds or is taking too long. Contact an admin or check the logs for the error.")
            return "timeout"
    except Exception as e:
        await log_exception("Startup Error", e)
        raise
    finally:
        starting_server = False
        await update_operation_buttons()


async def stop_minecraft_server(guild):
    global stopping_server

    if stopping_server:
        return "already_stopping"
    if starting_server:
        return "already_starting"

    stopping_server = True

    try:
        await update_operation_buttons()
        log_channel = await client.fetch_channel(
            CONFIG["discord"]["log_channel"]
        )

        if get_instance_status() != "running":
            return "already_off"

        await log_channel.send("🛑 Server stop initiated.")
        mc = guild.get_member(CONFIG["minecraft"]["bot_id"])

        if mc and str(mc.status) == "online":

            channel = guild.get_channel(
                CONFIG["minecraft"]["command_channel"]
            )

            if channel:
                await channel.send("stop")
                await log_channel.send("Stopped the Minecraft Server. Waiting for 120 seconds before stopping the instance.")

            await asyncio.sleep(120)

        ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        await log_channel.send("Instance has been stopped.")
        return "stopped"
    except Exception as e:
        await log_exception("Shutdown Error", e)
        raise
    finally:
        stopping_server = False
        await update_operation_buttons()


class ControlView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    def set_operation_buttons_disabled(self, disabled):
        for item in self.children:
            if item.custom_id in {"aws_start", "aws_stop", "toggle_autostop", "aws_refresh"}:
                item.disabled = disabled

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
        await client.log_channel.send(f"{interaction.user} issued command: Start")
        if not interaction.user.guild_permissions.administrator:

            return await interaction.response.send_message(
                "❌ Administrator permissions required.",
                ephemeral=True
            )

        await client.log_channel.send(f"{interaction.user} issued command: Start")
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

            elif result == "already_starting":
                await interaction.followup.send(
                    "⏳ Server startup is already in progress.",
                    ephemeral=True
                )

            elif result == "already_stopping":
                await interaction.followup.send(
                    "⏳ Server shutdown is already in progress.",
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

            elif result == "aws_timeout":
                await interaction.followup.send(
                    "❌ AWS instance startup timed out.",
                    ephemeral=True
                )

            elif result == "ssm_timeout":
                await interaction.followup.send(
                    "❌ EC2 started but SSM agent never became ready.",
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

        await client.log_channel.send(f"{interaction.user} issued command: Stop")
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

            elif result == "already_stopping":
                await interaction.followup.send(
                    "⏳ Server shutdown is already in progress.",
                    ephemeral=True
                )

            elif result == "already_starting":
                await interaction.followup.send(
                    "⏳ Server startup is already in progress.",
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
<<<<<<< Updated upstream
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ Administrator permissions required.",
                ephemeral=True
            )

=======
>>>>>>> Stashed changes
        await client.log_channel.send(f"{interaction.user} issued command: Refresh")
        await refresh_panel(interaction, self)
        await interaction.response.send_message(
            "🔄 Updated dashboard.",
            ephemeral=True
        )

    @discord.ui.button(
        label="🔀 Toggle AutoStop",
        style=discord.ButtonStyle.blurple,
        custom_id="toggle_autostop"
    )
    async def toggle_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ Administrator permissions required.",
                ephemeral=True
            )

        await client.log_channel.send(f"{interaction.user} issued command: Toggle")
        previous_autostop_state = CONFIG["autostop"]["enabled"]
        CONFIG["autostop"]["enabled"] = not previous_autostop_state

        try:
            await asyncio.to_thread(save_config, CONFIG)
        except Exception as e:
            CONFIG["autostop"]["enabled"] = previous_autostop_state
            await log_exception("Auto-Stop Save Error", e)
            return await interaction.response.send_message(
                "❌ Failed to save the Auto-Stop setting.",
                ephemeral=True
            )

        await interaction.response.send_message(
            f"🔄 Set autostop to {CONFIG['autostop']['enabled']}",
            ephemeral=True
        )
        await refresh_panel(interaction, self)
control_view = None
@client.command(name="start")
@commands.cooldown(
    1,
    60.0,
    commands.BucketType.guild
)
async def start_server(ctx):

    if ctx.author.id not in CONFIG["permissions"]["authorized_users"]:
        ctx.command.reset_cooldown(ctx)

        return await ctx.send("Not authorized.")

    result = await start_minecraft_server(
        ctx.guild
    )

    await ctx.send(f"Result: {result}")


@client.command(name="stop")
async def stop_server(ctx):

    if ctx.author.id not in CONFIG["permissions"]["authorized_users"]:
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
        CONFIG["discord"]["control_panel_channel"]
    )

    panel_message = await channel.send(
        embed=create_status_embed(ctx.guild),
        view=control_view
    )


@client.event
async def on_ready():
    global control_view
    client.log_channel = await client.fetch_channel(
        CONFIG["discord"]["log_channel"]
    )

    if control_view is None:
        control_view = ControlView()
    client.add_view(control_view)
    
    if not refresh_dashboard.is_running():
        refresh_dashboard.start()

    global panel_message

    try:
        channel = await client.fetch_channel(
            CONFIG["discord"]["control_panel_channel"]
        )

        async for msg in channel.history(limit=10):
            if msg.author.id == client.user.id and msg.embeds:
                panel_message = msg
                break

    except Exception:
        pass
    await client.log_channel.send(f"Logged in as {client.user}")
def clean_code(content):
    if content.startswith("```") and content.endswith("```"):
        return "\n".join(content.split("\n")[1:])[:-3]
    else:
        return content

@client.command(aliases = ['eval'])
async def evaluate(ctx, *, arg = None):
    if ctx.author.id not in CONFIG["permissions"]["authorized_users"][:3]:
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

@tasks.loop(minutes=DASHBOARD_REFRESH_MINUTES)
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

        if(CONFIG["autostop"]["enabled"]):
            mc_info = get_minecraft_info()

            if mc_info["online"]:
                if mc_info["players"] == 0:
                    idle_ticks += 1
                    print(f"Idle ticks: {idle_ticks}")

                    log_channel = panel_message.guild.get_channel(
                        CONFIG["discord"]["log_channel"]
                    )

                    if idle_ticks == CONFIG["autostop"]["max_idle_ticks"] - 1:
                        await log_channel.send(
                            "⚠️ No players detected.\n"
                            "Server will shut down in approximately 5 minutes if nobody joins."
                        )

                    if idle_ticks >= CONFIG["autostop"]["max_idle_ticks"]:
                        await log_channel.send(
                            "🛑 Auto-shutdown triggered due to inactivity."
                        )

                        # Reset before stopping so another refresh cannot duplicate it.
                        idle_ticks = 0
                        await stop_minecraft_server(panel_message.guild)
                else:
                    idle_ticks = 0  # reset if players join
            # If mcstatus cannot confirm the server is online, preserve the current
            # counter and skip idle evaluation for this refresh cycle.

    except Exception as e:
        print("Refresh error:", repr(e))
        await log_exception("Dashboard Refresh Error", e)


client.run(os.getenv("TOKEN"))
