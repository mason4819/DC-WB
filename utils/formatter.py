"""Message template rendering."""
import re

import discord

VARIABLES_HELP = "{user} {username} {displayname} {server} {member_count} {user_id}"
VARIABLES_DESCRIPTION = "{user}提及成員 {username}帳號 {displayname}顯示名稱 {server}伺服器 {member_count}成員數 {user_id}成員ID"

# Only these exact placeholders are replaced, so stray braces never raise errors.
_PATTERN = re.compile(r"\{(user|username|displayname|server|member_count|user_id)\}")


def clean_template(text: str) -> str:
    """Slash command inputs are single-line, so allow a literal \\n for line breaks."""
    return text.replace("\\n", "\n").strip()


def render_message(template: str, user: discord.abc.User, guild: discord.Guild) -> str:
    """Replace the supported variables in a welcome or leave template."""
    values = {
        "user": user.mention,
        "username": user.name,
        "displayname": user.display_name,
        "server": guild.name,
        "member_count": str(guild.member_count) if guild.member_count else "?",
        "user_id": str(user.id),
    }
    rendered = _PATTERN.sub(lambda match: values[match.group(1)], template)
    return rendered[:1900]  # stay below Discord's 2000 character message limit
