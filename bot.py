# =========================
# PART 1 — CORE
# =========================

import os
from threading import Thread
from datetime import datetime, timedelta
from collections import defaultdict

import nextcord
from nextcord.ext import commands, tasks
from flask import Flask

# ---------- CONFIG ----------
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = "F!"
PORT = int(os.getenv("PORT", 8080))

POKETWO_BOT_IDS = {716390085896962058}
IST_OFFSET = 5.5

ADMIN_ROLE_NAMES = ["Admin", "Moderator", "PoketwoHelper"]

# ---------- INTENTS ----------
INTENTS = nextcord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
INTENTS.guilds = True

# ---------- BOT ----------
bot = commands.Bot(
    command_prefix=PREFIX,
    intents=INTENTS,
    help_command=None
)

# ---------- FLASK ----------
app = Flask(__name__)

@app.route("/")
def home():
    return "Pokétwo Companion Bot alive."

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

Thread(target=run_flask, daemon=True).start()

# ---------- UTIL ----------
def ist_now():
    return datetime.utcnow() + timedelta(hours=IST_OFFSET)

def is_poketwo(msg):
    return msg.author and msg.author.id in POKETWO_BOT_IDS

def is_admin(member):
    return any(r.name in ADMIN_ROLE_NAMES for r in member.roles)

# Track last activity for auto-dex
last_active = defaultdict(lambda: None)

# User collections
user_collection = defaultdict(set)
user_shinies = defaultdict(set)

# =========================
# PART 2 — SPAWNS
# =========================

active_spawns = {}  # channel_id -> time

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} | Prefix {PREFIX}")

@bot.event
async def on_message(message):
    await bot.process_commands(message)

    if not is_poketwo(message):
        return

    if message.embeds:
        embed = message.embeds[0]
        title = (embed.title or "").lower()
        if "a wild pokémon has appeared" in title:
            active_spawns[message.channel.id] = ist_now()
            try:
                await message.channel.send("🟢 **Spawn detected** (assistant online)")
            except:
                pass

@tasks.loop(seconds=30)
async def spawn_cleanup():
    now = ist_now()
    for cid,t in list(active_spawns.items()):
        if (now - t).seconds > 300:
            active_spawns.pop(cid)

@spawn_cleanup.before_loop
async def before_spawn_cleanup():
    await bot.wait_until_ready()

spawn_cleanup.start()

# =========================
# PART 3 — DEX
# =========================

@bot.command()
async def mydex(ctx):
    total = len(user_collection[ctx.author.id])
    shiny = len(user_shinies[ctx.author.id])
    await ctx.reply(
        f"📘 **Dex Stats**\n"
        f"Total: {total}\n"
        f"Shiny: {shiny}"
    )

# =========================
# PART 4 — MARKET / TRADE
# =========================

user_shops = defaultdict(list)
trade_logs = defaultdict(list)

@bot.event
async def shop_listener(message):
    if not is_poketwo(message):
        return
    if message.embeds:
        embed = message.embeds[0]
        if "shop" in (embed.title or "").lower():
            if message.reference and message.reference.resolved:
                u = message.reference.resolved.author
                user_shops[u.id].append(embed)

@bot.command()
async def shopsummary(ctx):
    shops = user_shops.get(ctx.author.id)
    if not shops:
        return await ctx.reply("No shop detected yet.")
    e = shops[-1]
    await ctx.reply(e.description or "No description.")

@bot.command()
async def checktrade(ctx, give: float, take: float):
    ratio = give / take
    if ratio >= 0.8:
        await ctx.reply(f"✅ Fair trade ({ratio:.2f})")
    else:
        await ctx.reply(f"⚠️ Unfair trade ({ratio:.2f})")

# =========================
# PART 4.5 — P2 ASSISTANT++
# Auto Dex + Shop Index + Filters
# =========================

import re

# ---------- SHOP INDEX ----------
shop_index = defaultdict(list)
PRICE_REGEX = re.compile(r"(\d{1,9})")

LEGENDARY_KEYWORDS = {
    "entei", "suicune", "raikou",
    "mewtwo", "lugia", "ho-oh",
    "rayquaza", "dialga", "palkia",
    "giratina", "kyogre", "groudon"
}

# ---------- AUTO DEX LISTENER ----------
@bot.event
async def auto_dex_listener(message):
    if not is_poketwo(message):
        return

    content = (message.content or "").lower()

    if "you caught" in content and message.mentions:
        user = message.mentions[0]

        tokens = content.replace("!", "").replace(".", "").split()
        for t in tokens:
            if t.isalpha() and len(t) > 3:
                pokemon = t
                user_collection[user.id].add(pokemon)
                if "shiny" in content:
                    user_shinies[user.id].add(pokemon)
                last_active[user.id] = ist_now()
                break

# ---------- SHOP PARSER ----------
@bot.event
async def shop_index_listener(message):
    if not is_poketwo(message):
        return
    if not message.embeds:
        return

    embed = message.embeds[0]
    title = (embed.title or "").lower()
    if "shop" not in title:
        return
    if not message.reference or not message.reference.resolved:
        return

    seller = message.reference.resolved.author
    lines = (embed.description or "").split("\n")

    for line in lines:
        clean = line.lower()
        price_match = PRICE_REGEX.search(clean)
        if not price_match:
            continue
        price = int(price_match.group(1))
        shiny = "shiny" in clean
        gmax = "gmax" in clean or "gigantamax" in clean
        words = clean.replace("⭐","").replace("—"," ").split()
        for w in words:
            if w.isalpha() and len(w) > 3:
                shop_index[w].append({
                    "seller_id": seller.id,
                    "seller_name": seller.name,
                    "price": price,
                    "raw": line,
                    "channel_id": message.channel.id,
                    "timestamp": ist_now(),
                    "shiny": shiny,
                    "gmax": gmax
                })
                break

# ---------- SEARCH COMMANDS ----------
async def send_results(ctx, title, results):
    msg = f"🛒 **{title.upper()} — Listings**\n\n"
    for r in sorted(results, key=lambda x: x["price"])[:10]:
        flags = []
        if r["shiny"]:
            flags.append("✨ Shiny")
        if r["gmax"]:
            flags.append("💠 G-Max")
        flag_txt = f" ({', '.join(flags)})" if flags else ""
        msg += (
            f"• `{r['price']}` coins{flag_txt}\n"
            f"  Seller: `{r['seller_name']}`\n"
            f"  Channel: <#{r['channel_id']}>\n\n"
        )
    await ctx.reply(msg[:2000])

@bot.command(name="--n")
async def search_name(ctx, *, pokemon: str):
    name = pokemon.lower().strip()
    results = shop_index.get(name)
    if not results:
        return await ctx.reply(f"❌ No **{pokemon}** found.")
    await send_results(ctx, name, results)

@bot.command(name="--shiny")
async def search_shiny(ctx, *, pokemon: str):
    name = pokemon.lower().strip()
    results = [r for r in shop_index.get(name, []) if r["shiny"]]
    if not results:
        return await ctx.reply(f"✨ No shiny **{pokemon}** found.")
    await send_results(ctx, f"Shiny {name}", results)

@bot.command(name="--gmax")
async def search_gmax(ctx, *, pokemon: str):
    name = pokemon.lower().strip()
    results = [r for r in shop_index.get(name, []) if r["gmax"]]
    if not results:
        return await ctx.reply(f"💠 No Gigantamax **{pokemon}** found.")
    await send_results(ctx, f"G-Max {name}", results)

@bot.command(name="--p")
async def search_price(ctx, max_price: int, *, pokemon: str):
    name = pokemon.lower().strip()
    results = [r for r in shop_index.get(name, []) if r["price"] <= max_price]
    if not results:
        return await ctx.reply(f"❌ No **{pokemon}** under `{max_price}`.")
    await send_results(ctx, f"{name} ≤ {max_price}", results)

# =========================
# PART 5 — REMINDERS
# =========================

server_reminders = {}

@bot.command()
async def setreminder(ctx, time_ist: str):
    if not is_admin(ctx.author):
        return await ctx.reply("❌ Admin only")
    server_reminders[ctx.guild.id] = time_ist
    await ctx.reply(f"⏰ Reminder set for {time_ist} IST")

@tasks.loop(minutes=1)
async def reminder_loop():
    now = ist_now().strftime("%H:%M")
    for gid,t in server_reminders.items():
        if t == now:
            g = bot.get_guild(gid)
            if g:
                for m in g.members:
                    try:
                        await m.send("⏰ Pokétwo reminder!")
                    except:
                        pass

@reminder_loop.before_loop
async def before_reminders():
    await bot.wait_until_ready()

reminder_loop.start()

# =========================
# PART 5.5 — HELP
# =========================

HELP_TEXT = f"""
🧠 **Pokétwo Companion Bot — Full Guide**

Prefix: `{PREFIX}`
Slash: `/ping`

━━━━━━━━━━━━━━━━━━
🏓 CORE
━━━━━━━━━━━━━━━━━━
`F!ping`
→ Check bot latency

`F!help`
→ Show this help menu

━━━━━━━━━━━━━━━━━━
🌱 SPAWN SYSTEM
━━━━━━━━━━━━━━━━━━
• Automatically detects Pokétwo spawns
• Shows spawn alert in channel
• Uses IST-based timing
• 100% ToS-safe

━━━━━━━━━━━━━━━━━━
📘 DEX & COLLECTION
━━━━━━━━━━━━━━━━━━
`F!mydex`
→ Shows your dex stats (total / shiny)

━━━━━━━━━━━━━━━━━━
🛒 SHOP & MARKET
━━━━━━━━━━━━━━━━━━
`F!shopsummary`
→ Show last detected shop

`F!checktrade <you_give> <you_get>`
→ Fairness check

`F!--n <pokemon>`
→ Search Pokémon

`F!--shiny <pokemon>`
→ Shiny-only search

`F!--gmax <pokemon>`
→ Gigantamax-only search

`F!--p <max_price> <pokemon>`
→ Price filter

━━━━━━━━━━━━━━━━━━
⏰ REMINDERS (ADMIN ONLY)
━━━━━━━━━━━━━━━━━━
`F!setreminder HH:MM`

━━━━━━━━━━━━━━━━━━
"""

@bot.command(name="help")
async def help_cmd(ctx):
    await ctx.reply(HELP_TEXT, mention_author=False)

@bot.slash_command(
    name="help",
    description="Show full Pokétwo Companion Bot guide"
)
async def slash_help(interaction: nextcord.Interaction):
    await interaction.response.send_message(HELP_TEXT, ephemeral=True)

# =========================
# PART 6 — STATS + RUN
# =========================

catch_counts = defaultdict(int)

@bot.command()
async def ping(ctx):
    await ctx.reply(f"🏓 Pong `{round(bot.latency*1000)}ms`")

@bot.slash_command(name="ping", description="Latency check")
async def slash_ping(i: nextcord.Interaction):
    await i.response.send_message(
        f"🏓 Pong `{round(bot.latency*1000)}ms`",
        ephemeral=True
    )

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN missing")
    bot.run(TOKEN)
