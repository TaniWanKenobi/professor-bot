import os
import re
import json
import random
import threading
from datetime import datetime, timezone
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])

EXCLUDE_FILE = "exclude_list.json"
MENTORS_FILE = "mentors.json"
PLAN_FILE = "plan.json"
CHANNELS_FILE = "channels.json"
ADMIN_FILE = "admin.json"
WATCHERS_FILE = "watchers.json"

USAGE = (
    "*`/professor` — mentorship group channel manager*\n\n"
    "*Admin:*\n"
    "• `/professor admin set @you` — set admin (only admin can run all other commands)\n"
    "• `/professor admin show` — show current admin\n"
    "• `/professor admin clear` — clear admin\n\n"
    "*Reaction lookups:*\n"
    "• `/professor list <link> <emoji> [--exclude @u …]` — list reactors\n"
    "• `/professor random <link> <emoji> [--exclude @u …]` — list in random order\n"
    "• `/professor groups <N> <link> <emoji> [--exclude @u …]` — preview N random groups\n\n"
    "*Group channel workflow:*\n"
    "• `/professor plan <N> <link> <emoji>` — split reactors into N groups & save\n"
    "• `/professor plan show` — show plan with display names\n"
    "• `/professor plan clear` — discard plan\n"
    "• `/professor assign <group#> @mentor1 @mentor2` — assign mentors (2 preferred, 3 if needed)\n"
    "• `/professor assign show` — show assignments\n"
    "• `/professor assign clear` — clear all assignments\n"
    "• `/professor launch [prefix]` — create private channels, invite everyone, create canvas\n\n"
    "*Reaction watcher (DMs you when people react):*\n"
    "• `/professor watch <link> <emoji>` — watch a message for reactions\n"
    "• `/professor watch list` — show active watchers\n"
    "• `/professor watch remove <link> <emoji>` — stop watching\n"
    "• `/professor watch clear` — remove all watchers\n\n"
    "*Mentor list:*\n"
    "• `/professor mentor list` — show mentors\n"
    "• `/professor mentor add @u …` — add mentors (auto-excluded from participant pool)\n"
    "• `/professor mentor remove @u …` — remove mentors\n"
    "• `/professor mentor set @u1 @u2 …` — replace entire list\n"
    "• `/professor mentor clear` — clear list\n\n"
    "*Channel management:*\n"
    "• `/professor channels list` — list bot-created channels\n"
    "• `/professor channels add @u <group#>` — add someone to a group's channel\n"
    "• `/professor channels kick @u <group#>` — remove someone from a group's channel\n"
    "• `/professor channels archive` — archive all bot-created channels\n\n"
    "*Exclude list:*\n"
    "• `/professor exclude` — show excluded users\n"
    "• `/professor exclude add @u …` — add (also works inline with `--exclude`)\n"
    "• `/professor exclude remove @u …` — remove\n"
    "• `/professor exclude clear` — clear"
)

_ERROR_PREFIXES = (
    "Error", "Usage", "No active plan", "No channels", "No bot",
    "No mentors", "No watched", "Could not", "Mention at least",
    "Unknown", "Warning", "Only the current", "Only the admin",
    "Group ", "Groups ", "Exclude list is empty", "Already watching",
    "Not watching",
)

def _wrap_respond(base_respond, raw_text: str):
    def wrapped(text="", **kwargs):
        if any(text.startswith(p) for p in _ERROR_PREFIXES):
            text = f"{text}\n_Command:_ `/professor {raw_text}`"
        base_respond(text=text, **kwargs)
    return wrapped


# ---------- persistence ----------

def _load(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def _save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_exclude_list(): return _load(EXCLUDE_FILE, [])
def save_exclude_list(u): _save(EXCLUDE_FILE, list(set(u)))

def load_mentors(): return _load(MENTORS_FILE, [])
def save_mentors(u): _save(MENTORS_FILE, list(dict.fromkeys(u)))

def load_plan():
    data = _load(PLAN_FILE, None)
    if data is None:
        return None
    # Accept both list format (bot-generated) and dict format (hand-crafted)
    # List format:  {"groups": [{"id": 1, "participants": [...], "mentors": [...]}, ...]}
    # Dict format:  {"groups": {"1": {"participants": [...], "mentors": [...]}, ...}}
    if isinstance(data.get("groups"), dict):
        data["groups"] = [
            {"id": int(k), "participants": v["participants"], "mentors": v.get("mentors", [])}
            for k, v in sorted(data["groups"].items(), key=lambda x: int(x[0]))
        ]
    return data

def save_plan(p): _save(PLAN_FILE, p)

def load_channels(): return _load(CHANNELS_FILE, [])
def save_channels(c): _save(CHANNELS_FILE, c)

def load_admin() -> str | None: return _load(ADMIN_FILE, None)
def save_admin(uid: str | None): _save(ADMIN_FILE, uid)

def load_watchers(): return _load(WATCHERS_FILE, [])
def save_watchers(w): _save(WATCHERS_FILE, w)


# ---------- name resolution ----------

_name_cache: dict[str, str] = {}

def preload_names(client):
    """Bulk-fetch all workspace users into the cache in one API call."""
    try:
        cursor = None
        while True:
            kwargs = {"limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            resp = client.users_list(**kwargs)
            for user in resp.get("members", []):
                if user.get("deleted") or user.get("is_bot"):
                    continue
                profile = user.get("profile", {})
                name = profile.get("display_name") or profile.get("real_name") or user["id"]
                _name_cache[user["id"]] = name
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except Exception as e:
        print(f"[names] preload error: {e}")

def resolve_name(client, user_id: str) -> str:
    if user_id not in _name_cache:
        try:
            resp = client.users_info(user=user_id)
            profile = resp["user"]["profile"]
            _name_cache[user_id] = profile.get("display_name") or profile.get("real_name") or user_id
        except Exception:
            _name_cache[user_id] = user_id
    return _name_cache[user_id]


# ---------- helpers ----------

def parse_mentions(tokens: list[str]) -> list[str]:
    user_ids = []
    for token in tokens:
        m = re.search(r"<@([A-Za-z0-9]+)(?:\|[^>]*)?>", token)
        if m:
            user_ids.append(m.group(1))
    return user_ids

def parse_message_link(link: str) -> tuple[str | None, str | None]:
    m = re.search(r"/archives/([A-Z0-9]+)/p(\d+)", link)
    if not m:
        return None, None
    raw_ts = m.group(2)
    return m.group(1), raw_ts[:-6] + "." + raw_ts[-6:]

def get_reactors(client, channel: str, timestamp: str, emoji: str) -> list[str]:
    try:
        client.conversations_join(channel=channel)
    except Exception:
        pass
    resp = client.reactions_get(channel=channel, timestamp=timestamp, full=True)
    for reaction in resp["message"].get("reactions", []):
        if reaction["name"] == emoji:
            return reaction["users"]
    return []

def fmt(users: list[str]) -> str:
    return "  ".join(f"<@{u}>" for u in users)

def split_into_groups(users: list[str], n: int) -> list[list[str]]:
    shuffled = users.copy()
    random.shuffle(shuffled)
    groups: list[list[str]] = [[] for _ in range(n)]
    for i, user in enumerate(shuffled):
        groups[i % n].append(user)
    return groups

def get_user_group(user_id: str) -> int | None:
    """Return the group_id of the channel this user is in, or None."""
    for c in load_channels():
        if user_id in c.get("participants", []) or user_id in c.get("mentors", []):
            return c["group_id"]
    return None

def fmt_user(user_id: str) -> str:
    return f"<@{user_id}> ({user_id})"

def fmt_users(users: list[str]) -> str:
    return "\n".join(f"  • {fmt_user(u)}" for u in users)

def format_plan(plan, client=None) -> str:
    if not plan:
        return "No active plan. Run `/professor plan <N> <link> <emoji>` to create one."
    total = sum(len(g["participants"]) for g in plan["groups"])
    lines = [f"*Plan — {len(plan['groups'])} groups, {total} participants:*"]
    for g in plan["groups"]:
        m_str = fmt_users(g["mentors"]) if g["mentors"] else "  _none assigned_"
        lines.append(
            f"\n*Group {g['id']}* ({len(g['participants'])} participants):\n{fmt_users(g['participants'])}"
            f"\n  *Mentors:*\n{m_str}"
        )
    return "\n".join(lines)

def build_canvas_md(group_id: int, participants: list[str], mentors: list[str]) -> str:
    mentor_str = "  ".join(f"<@{u}>" for u in mentors) if mentors else "_None assigned_"
    rows = []
    for i in range(0, len(participants), 4):
        rows.append("  ".join(f"<@{u}>" for u in participants[i:i+4]))
    participant_str = "\n".join(rows)
    return (
        f"# Mentorship Group {group_id}\n\n"
        f"## Mentors ({len(mentors)})\n{mentor_str}\n\n"
        f"## Participants ({len(participants)})\n{participant_str}"
    )


# ---------- reaction watcher background flush (24h) ----------

def flush_watchers_job():
    """Runs every 24 hours. Re-checks all reactors live and DMs admin about anyone not yet in a group."""
    admin = load_admin()
    if admin:
        watchers = load_watchers()
        excluded = load_exclude_list()

        for w in watchers:
            try:
                resp = app.client.reactions_get(channel=w["channel"], timestamp=w["timestamp"], full=True)
                all_reactors = []
                for reaction in resp["message"].get("reactions", []):
                    if reaction["name"] == w["emoji"]:
                        all_reactors = reaction["users"]
                        break

                unplaced = [u for u in all_reactors if u not in excluded and not get_user_group(u)]

                if unplaced:
                    lines = [
                        f"*24h reminder — :{w['emoji']}: reactors not yet in a group ({len(unplaced)}):*",
                        f"_<{w['link']}|View message>_",
                    ]
                    for uid in unplaced:
                        lines.append(f"• <@{uid}> ({uid})")
                    lines.append(f"\nUse `/professor channels add @user <group#>` to add them.")
                    app.client.chat_postMessage(channel=admin, text="\n".join(lines))
                else:
                    app.client.chat_postMessage(
                        channel=admin,
                        text=f"✓ All :{w['emoji']}: reactors on <{w['link']}|watched message> are in a group.",
                    )
            except Exception as e:
                print(f"[watcher] flush error: {e}")

    threading.Timer(86400, flush_watchers_job).start()


# ---------- event: reaction_added ----------

@app.event("reaction_added")
def handle_reaction_added(event):
    if event.get("item", {}).get("type") != "message":
        return

    reaction = event.get("reaction", "")
    user_id = event.get("user", "")
    channel = event["item"]["channel"]
    ts = event["item"]["ts"]

    watchers = load_watchers()
    changed = False
    for w in watchers:
        if w["channel"] == channel and w["timestamp"] == ts and w["emoji"] == reaction:
            seen = w.get("seen", [])
            pending = w.get("pending", [])
            if user_id not in seen and user_id not in pending:
                w.setdefault("pending", []).append(user_id)
                changed = True
            break

    if changed:
        save_watchers(watchers)


# ---------- sub-handlers ----------

def handle_admin_cmd(parts: list[str], caller_id: str, respond):
    subparts = parts[1:]
    admin = load_admin()

    if not subparts or subparts[0].lower() == "show":
        if not admin:
            respond(text="No admin set. Use `/professor admin set @you`", response_type="ephemeral")
        else:
            respond(text=f"Admin: <@{admin}> — added to every channel on launch.", response_type="ephemeral")
        return

    if admin and caller_id != admin:
        respond(text="Only the current admin can change admin settings.", response_type="ephemeral")
        return

    sub = subparts[0].lower()

    if sub == "set":
        users = parse_mentions(subparts[1:])
        if not users:
            respond(text="Mention a user: `/professor admin set @you`", response_type="ephemeral")
            return
        save_admin(users[0])
        respond(text=f"Admin set to <@{users[0]}>.", response_type="ephemeral")

    elif sub == "clear":
        save_admin(None)
        respond(text="Admin cleared.", response_type="ephemeral")

    else:
        respond(text=f"Unknown admin subcommand `{sub}`.\n\n{USAGE}", response_type="ephemeral")


def handle_watch_cmd(parts: list[str], respond):
    subparts = parts[1:]
    watchers = load_watchers()

    if not subparts or subparts[0].lower() == "list":
        if not watchers:
            respond(text="No watched messages. Use `/professor watch <link> <emoji>` to add one.", response_type="ephemeral")
            return
        lines = [f"*Watching {len(watchers)} message(s) — DM sent every 5 min when new reactions arrive:*"]
        for w in watchers:
            seen = len(w.get("seen", []))
            pending = len(w.get("pending", []))
            lines.append(f"• :{w['emoji']}: on <{w['link']}|message> — {seen} notified, {pending} pending")
        respond(text="\n".join(lines), response_type="ephemeral")
        return

    if subparts[0].lower() == "clear":
        save_watchers([])
        respond(text="All watchers cleared.", response_type="ephemeral")
        return

    if subparts[0].lower() == "remove":
        if len(subparts) < 3:
            respond(text="Usage: `/professor watch remove <link> <emoji>`", response_type="ephemeral")
            return
        link, emoji = subparts[1], subparts[2].strip(":")
        channel, timestamp = parse_message_link(link)
        updated = [w for w in watchers if not (w["channel"] == channel and w["timestamp"] == timestamp and w["emoji"] == emoji)]
        if len(updated) == len(watchers):
            respond(text="Not watching that message/emoji combination.", response_type="ephemeral")
        else:
            save_watchers(updated)
            respond(text=f"Stopped watching :{emoji}: on that message.", response_type="ephemeral")
        return

    # /professor watch <link> <emoji>
    if len(subparts) < 2:
        respond(text="Usage: `/professor watch <message_link> <emoji>`", response_type="ephemeral")
        return

    link, emoji = subparts[0], subparts[1].strip(":")
    channel, timestamp = parse_message_link(link)
    if not channel or not timestamp:
        respond(text="Could not parse the message link.", response_type="ephemeral")
        return

    for w in watchers:
        if w["channel"] == channel and w["timestamp"] == timestamp and w["emoji"] == emoji:
            respond(text=f"Already watching that message for :{emoji}: reactions.", response_type="ephemeral")
            return

    watchers.append({
        "channel": channel,
        "timestamp": timestamp,
        "emoji": emoji,
        "link": link,
        "pending": [],
        "seen": [],
        "last_sent": None,
    })
    save_watchers(watchers)
    respond(
        text=f"Watching <{link}|message> for :{emoji}: reactions.\nYou'll be DM'd every 5 minutes with new reactors and whether they're already in a group.",
        response_type="ephemeral",
    )


def handle_mentor_cmd(parts: list[str], respond):
    mentors = load_mentors()
    subparts = parts[1:]

    if not subparts or subparts[0].lower() == "list":
        if not mentors:
            respond(text="No mentors stored. Use `/professor mentor add @user …`", response_type="ephemeral")
        else:
            respond(text=f"*Mentors ({len(mentors)}):*\n{fmt(mentors)}", response_type="ephemeral")
        return

    sub = subparts[0].lower()

    if sub == "add":
        new = parse_mentions(subparts[1:])
        if not new:
            respond(text="Mention at least one user: `/professor mentor add @user`", response_type="ephemeral")
            return
        updated = list(dict.fromkeys(mentors + new))
        save_mentors(updated)
        excluded = list(set(load_exclude_list() + new))
        save_exclude_list(excluded)
        respond(text=f"Added {len(new)} mentor(s). Total {len(updated)}:\n{fmt(updated)}\n_Also added to exclude list._", response_type="ephemeral")

    elif sub == "remove":
        rem = parse_mentions(subparts[1:])
        if not rem:
            respond(text="Mention at least one user: `/professor mentor remove @user`", response_type="ephemeral")
            return
        updated = [u for u in mentors if u not in rem]
        save_mentors(updated)
        respond(text=f"Removed {len(rem)}. Remaining {len(updated)}:\n{fmt(updated)}", response_type="ephemeral")

    elif sub == "set":
        new = parse_mentions(subparts[1:])
        if not new:
            respond(text="Mention at least one user: `/professor mentor set @u1 @u2 …`", response_type="ephemeral")
            return
        save_mentors(new)
        excluded = list(set(load_exclude_list() + new))
        save_exclude_list(excluded)
        respond(text=f"Mentor list set ({len(new)}):\n{fmt(new)}\n_All added to exclude list._", response_type="ephemeral")

    elif sub == "clear":
        save_mentors([])
        respond(text="Mentor list cleared.", response_type="ephemeral")

    else:
        respond(text=f"Unknown mentor subcommand `{sub}`.\n\n{USAGE}", response_type="ephemeral")


def handle_plan_cmd(parts: list[str], client, respond):
    subparts = parts[1:]

    if not subparts or subparts[0].lower() == "show":
        respond(text=format_plan(load_plan()), response_type="ephemeral")
        return

    if subparts[0].lower() == "clear":
        save_plan(None)
        respond(text="Plan cleared.", response_type="ephemeral")
        return

    if len(subparts) < 3:
        respond(text="Usage: `/professor plan <N> <message_link> <emoji>`", response_type="ephemeral")
        return

    try:
        n = int(subparts[0])
        if n < 1:
            raise ValueError
    except ValueError:
        respond(text="Error: `N` must be a positive integer.", response_type="ephemeral")
        return

    message_link = subparts[1]
    emoji = subparts[2].strip(":")

    channel, timestamp = parse_message_link(message_link)
    if not channel or not timestamp:
        respond(text="Could not parse the message link.", response_type="ephemeral")
        return

    excluded = load_exclude_list()
    participants = [u for u in get_reactors(client, channel, timestamp, emoji) if u not in excluded]

    if not participants:
        respond(text=f"No :{emoji}: reactors found (or all are excluded).", response_type="ephemeral")
        return

    plan = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {"link": message_link, "emoji": emoji},
        "groups": [
            {"id": i + 1, "participants": g, "mentors": []}
            for i, g in enumerate(split_into_groups(participants, n))
        ],
    }
    save_plan(plan)
    respond(
        text=f"Plan created: {n} groups, {len(participants)} participants.\n\n{format_plan(plan)}\n\nAssign mentors with `/professor assign <group#> @mentor1 @mentor2`",
        response_type="ephemeral",
    )


def handle_assign_cmd(parts: list[str], respond):
    plan = load_plan()
    if not plan:
        respond(text="No active plan. Run `/professor plan <N> <link> <emoji>` first.", response_type="ephemeral")
        return

    subparts = parts[1:]

    if not subparts or subparts[0].lower() == "show":
        respond(text=format_plan(plan), response_type="ephemeral")
        return

    if subparts[0].lower() == "clear":
        for g in plan["groups"]:
            g["mentors"] = []
        save_plan(plan)
        respond(text="All mentor assignments cleared.", response_type="ephemeral")
        return

    try:
        group_num = int(subparts[0])
    except ValueError:
        respond(text="Usage: `/professor assign <group_number> @mentor1 @mentor2`", response_type="ephemeral")
        return

    group = next((g for g in plan["groups"] if g["id"] == group_num), None)
    if not group:
        respond(text=f"Group {group_num} not found. Plan has {len(plan['groups'])} groups.", response_type="ephemeral")
        return

    mentors = parse_mentions(subparts[1:])
    if not mentors:
        respond(text="Mention at least one mentor: `/professor assign <group#> @mentor1 @mentor2`", response_type="ephemeral")
        return

    if len(mentors) > 3:
        respond(text=f"Warning: {len(mentors)} mentors assigned to group {group_num} (preferred: 2–3).", response_type="ephemeral")

    group["mentors"] = mentors
    save_plan(plan)

    unassigned = [g["id"] for g in plan["groups"] if not g["mentors"]]
    footer = f"\n\n_{len(unassigned)} group(s) still need mentors: {unassigned}_" if unassigned else "\n\n_All groups have mentors. Ready to `/professor launch`._"
    respond(text=f"Group {group_num} mentors set: {fmt(mentors)}{footer}", response_type="ephemeral")


def handle_launch_cmd(parts: list[str], client, respond):
    plan = load_plan()
    if not plan:
        respond(text="No active plan. Run `/professor plan <N> <link> <emoji>` first.", response_type="ephemeral")
        return

    unassigned = [g["id"] for g in plan["groups"] if not g["mentors"]]
    if unassigned:
        respond(
            text=f"Groups {unassigned} have no mentors assigned. Use `/professor assign` first.",
            response_type="ephemeral",
        )
        return

    prefix = parts[1].strip() if len(parts) > 1 else "gc"
    channels = load_channels()
    admin = load_admin()
    created, errors = [], []

    for g in plan["groups"]:
        name = f"{prefix}-group-{g['id']}"
        try:
            result = client.conversations_create(name=name, is_private=True)
            cid = result["channel"]["id"]

            if admin:
                client.conversations_invite(channel=cid, users=admin)

            members = [u for u in g["participants"] + g["mentors"] if u != admin]
            if members:
                client.conversations_invite(channel=cid, users=",".join(members))

            # Create canvas with group info
            try:
                canvas_md = build_canvas_md(g["id"], g["participants"], g["mentors"])
                client.api_call(
                    "conversations.canvases.create",
                    json={
                        "channel_id": cid,
                        "document_content": {"type": "markdown", "markdown": canvas_md},
                    },
                )
            except Exception as ce:
                print(f"[canvas] Group {g['id']}: {ce}")

            channels.append({
                "channel_id": cid,
                "channel_name": name,
                "group_id": g["id"],
                "participants": g["participants"],
                "mentors": g["mentors"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            created.append(f"• <#{cid}> (`{name}`) — {len(g['participants'])} participants + {len(g['mentors'])} mentors")
        except Exception as e:
            errors.append(f"• Group {g['id']} (`{name}`): {e}")

    save_channels(channels)
    lines = []
    if created:
        lines.append(f"*Created {len(created)} channel(s):*\n" + "\n".join(created))
    if errors:
        lines.append(f"*Failed ({len(errors)}):*\n" + "\n".join(errors))
    respond(text="\n\n".join(lines) or "Nothing created.", response_type="ephemeral")


def handle_channels_cmd(parts: list[str], client, respond):
    channels = load_channels()
    subparts = parts[1:]

    if not subparts or subparts[0].lower() == "list":
        if not channels:
            respond(text="No bot-created channels yet. Run `/professor launch` after planning.", response_type="ephemeral")
            return
        lines = [f"*Bot-managed channels ({len(channels)}):*"]
        for c in channels:
            lines.append(
                f"• <#{c['channel_id']}> — Group {c['group_id']}: "
                f"{len(c['participants'])} participants, {len(c['mentors'])} mentors"
            )
        respond(text="\n".join(lines), response_type="ephemeral")
        return

    sub = subparts[0].lower()

    if sub == "addadmin":
        admin = load_admin()
        if not admin:
            respond(text="No admin set. Use `/professor admin set @you` first.", response_type="ephemeral")
            return
        if not channels:
            respond(text="No bot-created channels yet.", response_type="ephemeral")
            return
        added, errors = [], []
        for c in channels:
            try:
                client.conversations_invite(channel=c["channel_id"], users=admin)
                added.append(f"<#{c['channel_id']}>")
            except Exception as e:
                err = str(e)
                if "already_in_channel" in err:
                    added.append(f"<#{c['channel_id']}> (already in)")
                else:
                    errors.append(f"<#{c['channel_id']}>: {err}")
        lines = []
        if added:
            lines.append(f"Added <@{admin}> to {len(added)} channel(s): " + "  ".join(added))
        if errors:
            lines.append("Errors:\n" + "\n".join(errors))
        respond(text="\n".join(lines), response_type="ephemeral")
        return

    if sub == "archive":
        if not channels:
            respond(text="No channels to archive.", response_type="ephemeral")
            return
        archived, errors = [], []
        for c in channels:
            try:
                client.conversations_archive(channel=c["channel_id"])
                archived.append(c["channel_name"])
            except Exception as e:
                errors.append(f"`{c['channel_name']}`: {e}")
        save_channels([])
        lines = []
        if archived:
            lines.append("Archived: " + ", ".join(f"`{n}`" for n in archived))
        if errors:
            lines.append("Errors:\n" + "\n".join(errors))
        respond(text="\n".join(lines), response_type="ephemeral")

    elif sub == "add":
        if len(subparts) < 3:
            respond(text="Usage: `/professor channels add @user <group#>`", response_type="ephemeral")
            return
        try:
            group_num = int(subparts[-1])
        except ValueError:
            respond(text="Usage: `/professor channels add @user <group#>`", response_type="ephemeral")
            return
        user_ids = parse_mentions(subparts[1:-1])
        if not user_ids:
            respond(text="Mention at least one user.", response_type="ephemeral")
            return
        ch = next((c for c in channels if c["group_id"] == group_num), None)
        if not ch:
            respond(text=f"No channel found for group {group_num}.", response_type="ephemeral")
            return
        try:
            client.conversations_invite(channel=ch["channel_id"], users=",".join(user_ids))
            respond(text=f"Added {fmt(user_ids)} to <#{ch['channel_id']}>.", response_type="ephemeral")
        except Exception as e:
            respond(text=f"Error: {e}", response_type="ephemeral")

    elif sub == "kick":
        if len(subparts) < 3:
            respond(text="Usage: `/professor channels kick @user <group#>`", response_type="ephemeral")
            return
        try:
            group_num = int(subparts[-1])
        except ValueError:
            respond(text="Usage: `/professor channels kick @user <group#>`", response_type="ephemeral")
            return
        user_ids = parse_mentions(subparts[1:-1])
        if not user_ids:
            respond(text="Mention at least one user.", response_type="ephemeral")
            return
        ch = next((c for c in channels if c["group_id"] == group_num), None)
        if not ch:
            respond(text=f"No channel found for group {group_num}.", response_type="ephemeral")
            return
        errors = []
        for uid in user_ids:
            try:
                client.conversations_kick(channel=ch["channel_id"], user=uid)
            except Exception as e:
                errors.append(str(e))
        if errors:
            respond(text="Errors: " + "; ".join(errors), response_type="ephemeral")
        else:
            respond(text=f"Removed {fmt(user_ids)} from <#{ch['channel_id']}>.", response_type="ephemeral")

    else:
        respond(text=f"Unknown channels subcommand `{sub}`.\n\n{USAGE}", response_type="ephemeral")


def handle_exclude_cmd(parts: list[str], respond):
    excluded = load_exclude_list()
    subparts = parts[1:]

    if not subparts or subparts[0].lower() == "list":
        if not excluded:
            respond(text="Exclude list is empty.", response_type="ephemeral")
        else:
            respond(text=f"*Excluded ({len(excluded)}):*\n{fmt(excluded)}", response_type="ephemeral")
        return

    sub = subparts[0].lower()

    if sub == "add":
        new = parse_mentions(subparts[1:])
        if not new:
            respond(text="Mention at least one user: `/professor exclude add @user`", response_type="ephemeral")
            return
        updated = list(set(excluded + new))
        save_exclude_list(updated)
        respond(text=f"Added {len(new)}. Excluded ({len(updated)}): {fmt(updated)}", response_type="ephemeral")

    elif sub == "remove":
        rem = parse_mentions(subparts[1:])
        if not rem:
            respond(text="Mention at least one user: `/professor exclude remove @user`", response_type="ephemeral")
            return
        updated = [u for u in excluded if u not in rem]
        save_exclude_list(updated)
        respond(text=f"Removed {len(rem)}. Excluded ({len(updated)}): {fmt(updated)}", response_type="ephemeral")

    elif sub == "clear":
        save_exclude_list([])
        respond(text="Exclude list cleared.", response_type="ephemeral")

    else:
        respond(text=f"Unknown exclude subcommand `{sub}`.\n\n{USAGE}", response_type="ephemeral")


def handle_reaction_cmd(mode: str, parts: list[str], client, respond):
    inline_exclude: list[str] = []
    if "--exclude" in parts:
        exc_idx = parts.index("--exclude")
        inline_exclude = parse_mentions(parts[exc_idx + 1:])
        parts = parts[:exc_idx]

    if mode == "list":
        if len(parts) < 3:
            respond(text="Usage: `/professor list <link> <emoji> [--exclude @u …]`", response_type="ephemeral")
            return
        message_link, emoji, n_groups = parts[1], parts[2].strip(":"), None
    elif mode == "random":
        if len(parts) < 3:
            respond(text="Usage: `/professor random <link> <emoji> [--exclude @u …]`", response_type="ephemeral")
            return
        message_link, emoji, n_groups = parts[1], parts[2].strip(":"), None
    elif mode == "groups":
        if len(parts) < 4:
            respond(text="Usage: `/professor groups <N> <link> <emoji> [--exclude @u …]`", response_type="ephemeral")
            return
        try:
            n_groups = int(parts[1])
            if n_groups < 1:
                raise ValueError
        except ValueError:
            respond(text="Error: `N` must be a positive integer.", response_type="ephemeral")
            return
        message_link, emoji = parts[2], parts[3].strip(":")

    excluded = load_exclude_list()
    if inline_exclude:
        excluded = list(set(excluded + inline_exclude))
        save_exclude_list(excluded)

    channel, timestamp = parse_message_link(message_link)
    if not channel or not timestamp:
        respond(text="Could not parse the message link. Copy it via *Copy link* in Slack.", response_type="ephemeral")
        return

    users = [u for u in get_reactors(client, channel, timestamp, emoji) if u not in excluded]
    if not users:
        respond(text=f"No :{emoji}: reactions found (or all reactors are excluded).", response_type="ephemeral")
        return

    if mode == "list":
        out = f"*:{emoji}: reactors ({len(users)}):*\n{fmt(users)}"
    elif mode == "random":
        shuffled = users.copy()
        random.shuffle(shuffled)
        out = f"*:{emoji}: reactors in random order ({len(users)}):*\n{fmt(shuffled)}"
    elif mode == "groups":
        groups = split_into_groups(users, n_groups)
        lines = [f"*:{emoji}: reactors in {n_groups} groups ({len(users)} total):*"]
        for i, g in enumerate(groups, 1):
            lines.append(f"*Group {i} ({len(g)}):* {fmt(g)}")
        out = "\n".join(lines)

    respond(text=out, response_type="ephemeral")


def handle_audit_cmd(parts: list[str], client, respond):
    if len(parts) < 3:
        respond(text="Usage: `/professor audit <message_link> <emoji>`", response_type="ephemeral")
        return

    message_link, emoji = parts[1], parts[2].strip(":")
    channel, timestamp = parse_message_link(message_link)
    if not channel or not timestamp:
        respond(text="Could not parse the message link.", response_type="ephemeral")
        return

    excluded = load_exclude_list()
    reactors = [u for u in get_reactors(client, channel, timestamp, emoji) if u not in excluded]

    if not reactors:
        respond(text=f"No :{emoji}: reactors found (or all are excluded).", response_type="ephemeral")
        return

    missing, placed = [], []
    for uid in reactors:
        gid = get_user_group(uid)
        if gid:
            placed.append((uid, gid))
        else:
            missing.append(uid)

    lines = [f"*Audit: :{emoji}: reactors ({len(reactors)} total, {len(excluded)} excluded)*"]
    lines.append(f"✓ In a channel: {len(placed)}  |  ✗ Missing: {len(missing)}")

    if missing:
        lines.append(f"\n*Not in any group channel ({len(missing)}):*")
        lines.append("\n".join(f"• {fmt_user(u)}" for u in missing))

    # Per-channel participant counts (excluding mentors and admin)
    channels = load_channels()
    if channels:
        mentors = set(load_mentors())
        admin = load_admin()
        if admin:
            mentors.add(admin)
        lines.append("\n*Participants per channel (excl. mentors & admin):*")
        for c in sorted(channels, key=lambda x: x["group_id"]):
            count = sum(1 for p in c["participants"] if p not in mentors)
            lines.append(f"• <#{c['channel_id']}> (Group {c['group_id']}): {count} participants")

    respond(text="\n".join(lines), response_type="ephemeral")


# ---------- main dispatch ----------

@app.command("/professor")
def handle_professor(ack, command, client, respond):
    ack()
    parts = command.get("text", "").strip().split()
    raw_text = command.get("text", "").strip()
    respond = _wrap_respond(respond, raw_text)

    if not parts:
        respond(text=USAGE, response_type="ephemeral")
        return

    caller_id = command.get("user_id", "")
    mode = parts[0].lower()
    admin = load_admin()

    # /professor admin show is always public
    if not (mode == "admin" and (not parts[1:] or parts[1].lower() == "show")):
        if admin and caller_id != admin:
            respond(text=f"Only the admin (<@{admin}>) can use this bot.", response_type="ephemeral")
            return

    try:
        if mode == "admin":
            handle_admin_cmd(parts, caller_id, respond)
        elif mode == "watch":
            handle_watch_cmd(parts, respond)
        elif mode == "mentor":
            handle_mentor_cmd(parts, respond)
        elif mode == "plan":
            handle_plan_cmd(parts, client, respond)
        elif mode == "assign":
            handle_assign_cmd(parts, respond)
        elif mode == "launch":
            handle_launch_cmd(parts, client, respond)
        elif mode == "channels":
            handle_channels_cmd(parts, client, respond)
        elif mode == "audit":
            handle_audit_cmd(parts, client, respond)
        elif mode == "exclude":
            handle_exclude_cmd(parts, respond)
        elif mode in ("list", "random", "groups"):
            handle_reaction_cmd(mode, parts, client, respond)
        else:
            respond(text=f"Unknown command `{mode}`.\n\n{USAGE}", response_type="ephemeral")
    except Exception as e:
        respond(text=f"Error: {e}", response_type="ephemeral")


if __name__ == "__main__":
    threading.Timer(86400, flush_watchers_job).start()
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
