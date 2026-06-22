import os
import discord
import boto3
from discord import app_commands
from dotenv import load_dotenv
from discord.ext import commands
import asyncio

load_dotenv()


# Initialize discord client
intents = discord.Intents.all()
intents.presences = True    # Presence Intent
intents.members = True      # Server Members Intent
intents.message_content = True # Message Content Intent
client = commands.Bot(intents=intents,command_prefix = "<@1518569496103616593> ",case_insensitive =True,strip_after_prefix = True)

# AWS configuration (if not using an EC2 IAM role)
ec2 = boto3.client('ec2', region_name='ap-south-1',
                   aws_access_key_id=os.getenv('AWS_ACCESS_KEY'),
                   aws_secret_access_key=os.getenv('AWS_SECRET_KEY'))

INSTANCE_ID = 'i-054fc6ad1aec4fd94'
ssm = boto3.client('ssm', region_name="ap-south-1",
                   aws_access_key_id=os.getenv('AWS_ACCESS_KEY'),
                   aws_secret_access_key=os.getenv('AWS_SECRET_KEY'))



@client.command(name="start", description="Starts the AWS EC2 instance")
@commands.cooldown(1, 60.0, commands.BucketType.guild)
async def start_server(ctx):
    uid = ctx.author.id
    l = [587584984097751040,789495762325078078,602330585654099969,657618021480660993]
    if uid not in l:
        ctx.command.reset_cooldown(ctx)
        return await ctx.send("https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSfGxgWWRJzn3wcsszGJ_nS7-N3uOWqCfS5v_mIL2KZQyVREWUHya5oWl4Z&s=10")
    
    mc = ctx.guild.get_member(1082588508591501343)
    if str(mc.status) == "online":
        return await ctx.send("The server is already running.")
    
    instance_active = get_instance_status() == 'running'

    if not instance_active:
        await ctx.send("Sending start request to AWS...")
        try:
            ec2.start_instances(InstanceIds=[INSTANCE_ID])
            await ctx.send("EC2 instance has been started successfully. Waiting for 30 seconds for proper boot.")
            await asyncio.sleep(30)
        
        except Exception as e:
            return await ctx.send(f"An error occurred: {e}")
        

    ssm.send_command(
                    InstanceIds=[INSTANCE_ID],
                    DocumentName="AWS-RunShellScript",
                    Parameters={
                        'commands': [
                            'cd /home/ubuntu/minecraft && ./start.sh'
                        ]
                    }
                )
    await ctx.send("The server will be on soon.")
    def check(before,after):
        return after.id == mc.id
    
    try: 
        before,after = await client.wait_for("presence_update",check=check,timeout = 100)
        return await ctx.send("Server is online!")
    
    except asyncio.TimeoutError:
        await ctx.send(f"Timed out, contact an admin or check the screens.")
@client.event
async def on_ready():
    channel = await client.fetch_channel(1020949894745296896)
    await channel.send("Logged in.")
    print(f'Logged in as {client.user}')

def get_instance_status():

    response =ec2.describe_instances( InstanceIds= [INSTANCE_ID] )

    instance = response ["Reservations"] [0] ["Instances"] [0]

    state = instance ["State"] ["Name"]
    return state
@client.command(name = "status")
async def awsstatus(ctx):
    state = get_instance_status()
    await ctx.send(state)

@client.command(name = "kekw")
async def kek(ctx,member: discord.Member = None):
    if member:
        return await ctx.send(member.status)
    
    mc = ctx.guild.get_member(1082588508591501343)

    await ctx.send(mc.status)

@client.command(name = "stop")
async def stop(ctx):
    uid = ctx.author.id
    l = [587584984097751040,789495762325078078,602330585654099969,657618021480660993]
    if uid not in l:
        return await ctx.send("https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSfGxgWWRJzn3wcsszGJ_nS7-N3uOWqCfS5v_mIL2KZQyVREWUHya5oWl4Z&s=10")
    instance_active = get_instance_status() == 'running'
    if not instance_active:
        return await ctx.send("Instance is already off.")
    
    mc = ctx.guild.get_member(1082588508591501343)
    if str(mc.status) == "online":
        channel = ctx.guild.get_channel(1197985694128296029)
        await channel.send('stop')
        await asyncio.sleep(10)
        await ctx.send("Stopped minecraft server.")
    

    await ctx.send("Sending stop request to AWS...")
    try:
        ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        await ctx.send("EC2 instance has been stopped successfully.")
    
    except Exception as e:
        return await ctx.send(f"An error occurred: {e}")
    
class ControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # 'None' keeps buttons active permanently

    # Button 1: Start Server
    @discord.ui.button(label="Start Server", style=discord.ButtonStyle.green, custom_id="btn_start")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ You must be an Administrator to use this.", ephemeral=True)
            
        await interaction.response.send_message("⚡ Processing AWS Start Request...", ephemeral=True)
        await interaction.response.send_message("Sending start request to AWS...")
        try:
            ec2.start_instances(InstanceIds=[INSTANCE_ID])
            await interaction.response.send_message("EC2 instance has been started successfully. Waiting for 30 seconds for proper boot.")
            await asyncio.sleep(30)
        
        except Exception as e:
            return await interaction.response.send_message(f"An error occurred: {e}")
        

    # Button 2: Stop Server
    @discord.ui.button(label="Stop Server", style=discord.ButtonStyle.red, custom_id="btn_stop")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ You must be an Administrator to use this.", ephemeral=True)
            
        await interaction.response.send_message("🛑 Processing AWS Stop Request...", ephemeral=True)
        # Place your ec2.stop_instances code execution here

    # Button 3: Check Status
    @discord.ui.button(label="Status", style=discord.ButtonStyle.blurple, custom_id="btn_status")
    async def status_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Let anyone check status (no admin requirement)
        await interaction.response.send_message("🔍 Fetching AWS Instance Status...", ephemeral=True)
        # Place your ec2.describe_instances code execution here

@client.command()
async def setup(ctx):
    channel = ctx.guild.get_channel(1518599666734727315)

    embed = discord.Embed(
        title="🖥️ AWS EC2 Management Panel",
        description="Use the interactive buttons below to manage the cloud server deployment lifecycle.",
        color=discord.Color.blue()
    )
    embed.add_field(name="Instance ID", value="`i-0123456789abcdef0`", inline=False)
    embed.add_field(name="Access Control", value="Modification actions are strictly locked to **Administrators**.", inline=False)
    embed.set_footer(text="AWS Controller Bot • Cloud Automation")

    # Send the embed paired with the UI view
    await channel.send(embed=embed, view=ControlView())
    

client.run(os.getenv('TOKEN'))
