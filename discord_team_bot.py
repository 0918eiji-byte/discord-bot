"""
Discord チーム管理ボット v3
============================

【ロール構成】
  管理者ロール   … サーバー管理者。ボット設定コマンドが使える
  チームリーダー … 1つだけ作る共通リーダーロール
                   ＋ チームロールも持つことで、そのチームを管理できる
  チームメンバー … 各チームの「チームロール」のみ持つ

【例】
  ロール: 「チームリーダー」「チームA」「チームB」
  → 「チームリーダー」+「チームA」を持つ人 → チームAを管理できる
  → 「チームリーダー」+「チームB」を持つ人 → チームBを管理できる

【チャンネル構成】
  リーダー用チャンネル … リーダーが @メンション 加入/脱退 で操作
  脱退申請チャンネル   … メンバーが「脱退」と書くだけで自分のロールが外れる
                         リーダーが @メンション 脱退 でメンバーを脱退させることも可能

【管理者コマンド（プレフィックス: !）】
  !setup_leader #リーダーch #脱退ch
      チャンネルを登録

  !set_leader_role @ロール
      共通チームリーダーロールを設定

  !register_team @チームロール
      チームロールを登録

  !unregister_team @チームロール
      チーム登録を削除

  !list_teams
      設定一覧を表示
"""

import discord
from discord.ext import commands
import json
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

CONFIG_FILE = "config.json"


# ============================================================
# 設定の読み書き
# ============================================================

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_config(data: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def guild_cfg(config: dict, guild_id: int) -> dict:
    gid = str(guild_id)
    if gid not in config:
        config[gid] = {
            "leader_channel": None,   # リーダー操作用チャンネルID
            "leave_channel": None,    # 脱退申請チャンネルID
            "leader_role": None,      # 共通チームリーダーロールID
            "teams": []               # [team_role_id, ...] チームロールIDのリスト
        }
    # 古い形式との互換性
    if "teams" in config[gid] and isinstance(config[gid]["teams"], dict):
        config[gid]["teams"] = list(config[gid]["teams"].keys())
    if "leader_role" not in config[gid]:
        config[gid]["leader_role"] = None
    return config[gid]


# ============================================================
# ボット初期化
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
config = load_config()


# ============================================================
# ユーティリティ
# ============================================================

def is_leader(member: discord.Member, cfg: dict) -> bool:
    """メンバーがチームリーダーロールを持っているか"""
    if not cfg.get("leader_role"):
        return False
    return int(cfg["leader_role"]) in {r.id for r in member.roles}

def get_managed_team_roles(member: discord.Member, cfg: dict) -> list:
    """
    リーダーが管理できるチームロールを返す
    → チームリーダーロール＋チームロールを両方持っている場合、そのチームロールを返す
    """
    if not is_leader(member, cfg):
        return []
    member_role_ids = {r.id for r in member.roles}
    result = []
    for team_role_id in cfg.get("teams", []):
        if int(team_role_id) in member_role_ids:
            role = member.guild.get_role(int(team_role_id))
            if role:
                result.append(role)
    return result

def get_member_team_roles(member: discord.Member, cfg: dict) -> list:
    """メンバーが持っているチームロールの一覧を返す（脱退用）"""
    team_role_ids = {int(rid) for rid in cfg.get("teams", [])}
    return [r for r in member.roles if r.id in team_role_ids]

async def safe_add_role(channel, target: discord.Member, role: discord.Role, actor: discord.Member):
    try:
        await target.add_roles(role, reason=f"{actor} がチームロールを付与")
        return True
    except discord.Forbidden:
        await channel.send(
            f"❌ ロール `{role.name}` を付与できません。ボットのロールが対象ロールより上位か確認してください。"
        )
        return False

async def safe_remove_role(channel, target: discord.Member, role: discord.Role, actor: discord.Member):
    try:
        await target.remove_roles(role, reason=f"{actor} がチームロールを剥奪")
        return True
    except discord.Forbidden:
        await channel.send(
            f"❌ ロール `{role.name}` を剥奪できません。ボットのロールが対象ロールより上位か確認してください。"
        )
        return False


# ============================================================
# メッセージイベント
# ============================================================

@bot.event
async def on_ready():
    print(f"✅ ボット起動: {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    await bot.process_commands(message)

    guild = message.guild
    if guild is None:
        return

    cfg = guild_cfg(config, guild.id)
    content = message.content.strip()

    # --------------------------------------------------------
    # ① リーダー用チャンネル：「@メンション 加入/脱退」
    # --------------------------------------------------------
    if cfg["leader_channel"] and message.channel.id == int(cfg["leader_channel"]):
        if not message.mentions:
            return

        # リーダーチェック
        if not is_leader(message.author, cfg):
            await message.channel.send(
                f"⚠️ {message.author.mention} はチームリーダーロールを持っていません。"
            )
            return

        # 管理できるチームロールを取得（自分が所属しているチームのみ）
        team_roles = get_managed_team_roles(message.author, cfg)
        if not team_roles:
            await message.channel.send(
                f"⚠️ {message.author.mention} はチームリーダーロールを持っていますが、"
                f"チームロールが付与されていません。管理者にチームロールの付与を依頼してください。"
            )
            return

        is_join  = "加入" in content
        is_leave = "脱退" in content

        if not is_join and not is_leave:
            await message.channel.send(
                "⚠️ 「加入」または「脱退」というキーワードを含めてください。\n"
                "例: `@ユーザー 加入` / `@ユーザー 脱退`"
            )
            return

        for target in message.mentions:
            if target.bot:
                continue
            for role in team_roles:
                if is_join:
                    if role in target.roles:
                        await message.channel.send(
                            f"ℹ️ {target.mention} はすでに **{role.name}** に加入しています。"
                        )
                    else:
                        ok = await safe_add_role(message.channel, target, role, message.author)
                        if ok:
                            await message.channel.send(
                                f"✅ {target.mention} を **{role.name}** に加入させました！"
                            )
                elif is_leave:
                    if role not in target.roles:
                        await message.channel.send(
                            f"ℹ️ {target.mention} は **{role.name}** に加入していません。"
                        )
                    else:
                        ok = await safe_remove_role(message.channel, target, role, message.author)
                        if ok:
                            await message.channel.send(
                                f"✅ {target.mention} を **{role.name}** から脱退させました。"
                            )
        return

    # --------------------------------------------------------
    # ② 脱退申請チャンネル
    # --------------------------------------------------------
    if cfg["leave_channel"] and message.channel.id == int(cfg["leave_channel"]):
        if "脱退" not in content:
            return

        # リーダーが @メンション 脱退 → メンションされた人を脱退させる
        if is_leader(message.author, cfg) and message.mentions:
            team_roles = get_managed_team_roles(message.author, cfg)
            for target in message.mentions:
                if target.bot:
                    continue
                removed = []
                for role in team_roles:
                    if role in target.roles:
                        ok = await safe_remove_role(message.channel, target, role, message.author)
                        if ok:
                            removed.append(role.name)
                if removed:
                    await message.channel.send(
                        f"✅ {target.mention} を **{', '.join(removed)}** から脱退させました。"
                    )
                else:
                    await message.channel.send(
                        f"ℹ️ {target.mention} は該当するチームロールを持っていません。"
                    )
            return

        # リーダーがメンションなしで「脱退」→ 警告
        if is_leader(message.author, cfg):
            await message.channel.send(
                f"⚠️ {message.author.mention} はチームリーダーのため、この方法では脱退できません。\n"
                f"メンバーを脱退させる場合は `@ユーザー 脱退` と入力してください。"
            )
            return

        # 一般メンバーの自己脱退
        member_team_roles = get_member_team_roles(message.author, cfg)
        if not member_team_roles:
            await message.channel.send(
                f"ℹ️ {message.author.mention} は現在どのチームにも加入していません。"
            )
            return

        removed = []
        for role in member_team_roles:
            ok = await safe_remove_role(message.channel, message.author, role, message.author)
            if ok:
                removed.append(role.name)

        if removed:
            await message.channel.send(
                f"✅ {message.author.mention} が **{', '.join(removed)}** から脱退しました。"
            )
        return


# ============================================================
# 管理者コマンド
# ============================================================

@bot.command(name="setup_leader")
@commands.has_permissions(administrator=True)
async def setup_leader(ctx, leader_ch: discord.TextChannel, leave_ch: discord.TextChannel):
    """チャンネルを登録（管理者のみ）: !setup_leader #リーダーch #脱退ch"""
    cfg = guild_cfg(config, ctx.guild.id)
    cfg["leader_channel"] = str(leader_ch.id)
    cfg["leave_channel"]  = str(leave_ch.id)
    save_config(config)
    await ctx.send(
        f"✅ チャンネル設定完了！\n"
        f"　リーダー用: {leader_ch.mention}\n"
        f"　脱退申請用: {leave_ch.mention}"
    )


@bot.command(name="set_leader_role")
@commands.has_permissions(administrator=True)
async def set_leader_role(ctx, role: discord.Role):
    """共通チームリーダーロールを設定（管理者のみ）: !set_leader_role @ロール"""
    cfg = guild_cfg(config, ctx.guild.id)
    cfg["leader_role"] = str(role.id)
    save_config(config)
    await ctx.send(
        f"✅ チームリーダーロールを **{role.name}** に設定しました！\n"
        f"　このロール＋チームロールを持つ人がチームを管理できます。"
    )


@bot.command(name="register_team")
@commands.has_permissions(administrator=True)
async def register_team(ctx, team_role: discord.Role):
    """チームロールを登録（管理者のみ）: !register_team @チームロール"""
    cfg = guild_cfg(config, ctx.guild.id)
    if str(team_role.id) not in cfg["teams"]:
        cfg["teams"].append(str(team_role.id))
        save_config(config)
        await ctx.send(f"✅ **{team_role.name}** をチームロールとして登録しました！")
    else:
        await ctx.send(f"ℹ️ **{team_role.name}** はすでに登録されています。")


@bot.command(name="unregister_team")
@commands.has_permissions(administrator=True)
async def unregister_team(ctx, team_role: discord.Role):
    """チーム登録を削除（管理者のみ）: !unregister_team @チームロール"""
    cfg = guild_cfg(config, ctx.guild.id)
    if str(team_role.id) in cfg["teams"]:
        cfg["teams"].remove(str(team_role.id))
        save_config(config)
        await ctx.send(f"🗑️ **{team_role.name}** の登録を削除しました。")
    else:
        await ctx.send(f"⚠️ **{team_role.name}** は登録されていません。")


@bot.command(name="list_teams")
@commands.has_permissions(administrator=True)
async def list_teams(ctx):
    """設定一覧を表示（管理者のみ）"""
    cfg = guild_cfg(config, ctx.guild.id)

    leader_ch_id  = cfg.get("leader_channel")
    leave_ch_id   = cfg.get("leave_channel")
    leader_role_id = cfg.get("leader_role")

    leader_ch   = f"<#{leader_ch_id}>"   if leader_ch_id   else "未設定"
    leave_ch    = f"<#{leave_ch_id}>"    if leave_ch_id    else "未設定"
    lr          = ctx.guild.get_role(int(leader_role_id)) if leader_role_id else None
    leader_role = lr.name if lr else "未設定"

    lines = [
        "📋 **現在の設定**",
        f"　リーダー用チャンネル: {leader_ch}",
        f"　脱退申請チャンネル: {leave_ch}",
        f"　チームリーダーロール: **{leader_role}**",
        "",
        "**登録済みチームロール**"
    ]
    if not cfg.get("teams"):
        lines.append("　（なし）")
    else:
        for team_rid in cfg["teams"]:
            tr = ctx.guild.get_role(int(team_rid))
            tn = tr.name if tr else f"削除済み(ID:{team_rid})"
            lines.append(f"　・**{tn}**")

    await ctx.send("\n".join(lines))


@bot.command(name="help")
async def help_cmd(ctx):
    """ヘルプを表示"""
    is_admin = ctx.author.guild_permissions.administrator
    lines = [
        "**📖 チーム管理ボット ヘルプ**",
        "",
        "**【チームリーダー向け】リーダー用チャンネルで：**",
        "　`@ユーザー 加入` → そのユーザーに自分のチームロールを付与",
        "　`@ユーザー 脱退` → そのユーザーから自分のチームロールを剥奪",
        "",
        "**【チームメンバー向け】脱退申請チャンネルで：**",
        "　`脱退` → 自分のチームロールを全て削除",
        "　`@ユーザー 脱退`（リーダーのみ）→ 対象者のチームロールを削除",
    ]
    if is_admin:
        lines += [
            "",
            "**【管理者向けコマンド】**",
            "　`!setup_leader #リーダーch #脱退ch` → チャンネルを登録",
            "　`!set_leader_role @ロール` → 共通リーダーロールを設定",
            "　`!register_team @チームロール` → チームロールを登録",
            "　`!unregister_team @チームロール` → チームロールを削除",
            "　`!list_teams` → 設定一覧を表示",
        ]
    await ctx.send("\n".join(lines))


# ============================================================
# エラーハンドリング
# ============================================================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 このコマンドは管理者のみ使用できます。")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("⚠️ 指定されたメンバーが見つかりません。")
    elif isinstance(error, commands.RoleNotFound):
        await ctx.send("⚠️ 指定されたロールが見つかりません。")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠️ 引数が不足しています。`!help` で使い方を確認してください。")
    elif isinstance(error, commands.ChannelNotFound):
        await ctx.send("⚠️ 指定されたチャンネルが見つかりません。")
    else:
        raise error


# ============================================================
# 起動
# ============================================================

if __name__ == "__main__":
    if not TOKEN:
        print("❌ エラー: .env に DISCORD_TOKEN が設定されていません。")
    else:
        bot.run(TOKEN)
