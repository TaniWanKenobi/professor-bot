import os
import re
import json
import random
import threading
from datetime import datetime, timezone
from flask import Flask, request, jsonify
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
MANUAL_FILE = "manual_participants.json"
SLICE_FILE = "slice.json"
SLICE_REMOVED_FILE = "slice_removed.json"

USAGE = (
    "*`/professor` — mentorship group channel manager*\n\n"
    "*Admin:*\n"
    "• `/professor admin set @you` — set admin (only admin can run all other commands)\n"
    "• `/professor admin show` — show current admin (public)\n"
    "• `/professor admin clear` — clear admin\n\n"
    "*Reaction lookups:*\n"
    "• `/professor list <link> <emoji> [--exclude @u …]` — list reactors\n"
    "• `/professor random <link> <emoji> [--exclude @u …]` — list in random order\n"
    "• `/professor groups <N> <link> <emoji> [--exclude @u …]` — preview N random groups\n\n"
    "*Group channel workflow:*\n"
    "• `/professor plan <N> <link> <emoji>` — split reactors into N groups & save\n"
    "• `/professor plan show` — show plan with @mentions and Slack IDs\n"
    "• `/professor plan clear` — discard plan\n"
    "• `/professor assign <group#> @mentor1 @mentor2` — assign mentors (2 preferred, 3 if needed)\n"
    "• `/professor assign show` — show current assignments\n"
    "• `/professor assign clear` — clear all assignments\n"
    "• `/professor launch [prefix]` — create private channels, set topic, create canvas, invite everyone\n\n"
    "*Reaction watcher (DMs you every 24h about unplaced reactors + mentee counts per channel):*\n"
    "• `/professor watch <link> <emoji>` — watch a message for reactions\n"
    "• `/professor watch list` — show active watchers\n"
    "• `/professor watch run` — manually trigger the 24h check now\n"
    "• `/professor watch remove <link> <emoji>` — stop watching a message\n"
    "• `/professor watch clear` — remove all watchers\n\n"
    "*Audit:*\n"
    "• `/professor audit` — check who reacted but isn't in any channel (defaults to signup message)\n"
    "• `/professor audit <link> <emoji>` — audit a custom message\n\n"
    "*Mentor list:*\n"
    "• `/professor mentor list` — show mentors\n"
    "• `/professor mentor add @u …` — add mentors (auto-added to exclude list)\n"
    "• `/professor mentor remove @u …` — remove mentors\n"
    "• `/professor mentor set @u1 @u2 …` — replace entire list\n"
    "• `/professor mentor clear` — clear list\n\n"
    "*Group activity slice (remove inactive members):*\n"
    "• `/professor mentor slice <message>` — post a ✅ react prompt to all group channels (message required)\n"
    "• `/professor mentor slice --test <message>` — test in current channel only, doesn't save to slice.json\n"
    "• `/professor mentor finish` — DM you non-reactors per group with Remove buttons\n\n"
    "*Channel management:*\n"
    "• `/professor channels list` — list bot-created channels\n"
    "• `/professor channels add @u <group#>` — add someone to a group's channel & plan\n"
    "• `/professor channels kick @u <group#>` — remove someone from a group's channel\n"
    "• `/professor channels sync` — pull live Slack members into plan (fixes manual additions)\n"
    "• `/professor channels addadmin` — add admin to all channels\n"
    "• `/professor channels settopic` — set mentor names as topic on all channels\n"
    "• `/professor channels rename` — rename channels to Hideout-一 … Hideout-十二\n"
    "• `/professor channels archive` — archive all bot-created channels\n\n"
    "*Announce:*\n"
    "• `/professor announce <message>` — post a message to all channels\n"
    "• `/professor here [message]` — @here ping in the channel you run it from\n"
    "• `/professor channel [message]` — @channel ping in the channel you run it from\n\n"
    "*Delete:*\n"
    "• `/professor delete <message_link>` — delete a specific bot message\n"
    "• `/professor delete slice` — delete all slice messages from all channels\n\n"
    "*Manual participants (website signups, treated same as Slack reactors):*\n"
    "• `/professor manual list` — show manually added participants\n"
    "• `/professor manual add @u …` — add someone manually\n"
    "• `/professor manual remove @u …` — remove\n"
    "• `/professor manual clear` — clear manual list\n\n"
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

def load_manual_participants(): return _load(MANUAL_FILE, [])
def save_manual_participants(u): _save(MANUAL_FILE, list(dict.fromkeys(u)))

def load_slice(): return _load(SLICE_FILE, [])
def save_slice(s): _save(SLICE_FILE, s)

def load_slice_removed(): return _load(SLICE_REMOVED_FILE, [])
def save_slice_removed(s): _save(SLICE_REMOVED_FILE, s)



# ---------- Flask HTTP API ----------

flask_app = Flask(__name__)

@flask_app.route("/manual-add", methods=["POST"])
def api_manual_add():
    data = request.get_json(silent=True) or {}
    secret = os.environ.get("PROFESSOR_API_SECRET", "")
    if not secret or data.get("secret") != secret:
        return jsonify({"error": "unauthorized"}), 401
    user_id = data.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    manual = load_manual_participants()
    if user_id not in manual:
        manual.append(user_id)
        save_manual_participants(manual)
    return jsonify({"ok": True, "user_id": user_id})

@flask_app.route("/manual-remove", methods=["POST"])
def api_manual_remove():
    data = request.get_json(silent=True) or {}
    secret = os.environ.get("PROFESSOR_API_SECRET", "")
    if not secret or data.get("secret") != secret:
        return jsonify({"error": "unauthorized"}), 401
    user_id = data.get("user_id", "").strip()
    manual = [u for u in load_manual_participants() if u != user_id]
    save_manual_participants(manual)
    return jsonify({"ok": True})

@flask_app.route("/health", methods=["GET"])
def api_health():
    return jsonify({"ok": True})

def start_flask():
    port = int(os.environ.get("PROFESSOR_API_PORT", "5001"))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


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

def _get_channel_counts(bot_channels, mentors_set, admin) -> dict[int, int]:
    counts = {}
    for c in bot_channels:
        try:
            cursor = None
            members = []
            while True:
                kwargs = {"channel": c["channel_id"], "limit": 200}
                if cursor:
                    kwargs["cursor"] = cursor
                resp = app.client.conversations_members(**kwargs)
                members.extend(resp.get("members", []))
                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            counts[c["group_id"]] = sum(1 for u in members if u not in mentors_set and u != admin)
        except Exception:
            counts[c["group_id"]] = -1
    return counts

def _build_unplaced_blocks(unplaced: list[str], bot_channels: list, channel_counts: dict, link: str, emoji: str) -> list:
    sorted_channels = sorted(bot_channels, key=lambda c: channel_counts.get(c["group_id"], 999))
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Unplaced reactors ({len(unplaced)})", "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f":{emoji}: reactions on <{link}|signup message> — select a group to add each person"}]},
        {"type": "divider"},
    ]
    for uid in unplaced:
        options = [
            {
                "text": {"type": "plain_text", "text": f"Group {c['group_id']} — {channel_counts.get(c['group_id'], '?')} mentees"},
                "value": f"{uid}:{c['group_id']}",
            }
            for c in sorted_channels
        ]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"<@{uid}> `{uid}`"},
            "accessory": {
                "type": "static_select",
                "placeholder": {"type": "plain_text", "text": "Add to group →"},
                "options": options,
                "action_id": "add_to_group",
            },
        })
    return blocks

def _do_watcher_flush():
    """Core flush logic shared by scheduled and manual runs."""
    admin = load_admin()
    if not admin:
        return
    watchers = load_watchers()
    excluded = load_exclude_list()
    bot_channels = load_channels()
    mentors_set = set(load_mentors())
    channel_counts = _get_channel_counts(bot_channels, mentors_set, admin)

    for w in watchers:
        try:
            resp = app.client.reactions_get(channel=w["channel"], timestamp=w["timestamp"], full=True)
            all_reactors = []
            for reaction in resp["message"].get("reactions", []):
                if reaction["name"] == w["emoji"]:
                    all_reactors = reaction["users"]
                    break
            # Merge manual participants (website signups) with Slack reactors
            all_participants = list(dict.fromkeys(all_reactors + load_manual_participants()))
            unplaced = [u for u in all_participants if u not in excluded and not get_user_group(u)]
            if unplaced:
                blocks = _build_unplaced_blocks(unplaced, bot_channels, channel_counts, w["link"], w["emoji"])
                app.client.chat_postMessage(channel=admin, text=f"Unplaced reactors ({len(unplaced)})", blocks=blocks)
            else:
                summary = "\n".join(
                    f"• <#{c['channel_id']}> Group {c['group_id']}: {channel_counts.get(c['group_id'], '?')} mentees"
                    for c in sorted(bot_channels, key=lambda x: x["group_id"])
                )
                app.client.chat_postMessage(
                    channel=admin,
                    text=f"✓ All reactors are placed.\n\n*Mentee counts:*\n{summary}",
                )
        except Exception as e:
            print(f"[watcher] flush error: {e}")

def flush_watchers_job_once():
    _do_watcher_flush()


# ---------- action: add to group dropdown ----------

@app.action("add_to_group")
def handle_add_to_group(ack, action, client, body):
    ack()
    selected = action["selected_option"]["value"]
    uid, gid_str = selected.rsplit(":", 1)
    gid = int(gid_str)
    dm_channel = body["channel"]["id"]

    channels = load_channels()
    ch = next((c for c in channels if c["group_id"] == gid), None)
    if not ch:
        client.chat_postMessage(channel=dm_channel, text=f"Error: no channel found for group {gid}.")
        return

    try:
        client.conversations_invite(channel=ch["channel_id"], users=uid)

        if uid not in ch["participants"] and uid not in ch.get("mentors", []):
            ch["participants"].append(uid)
            save_channels(channels)

        plan = load_plan()
        if plan:
            for g in plan["groups"]:
                if g["id"] == gid:
                    if uid not in g["participants"] and uid not in g["mentors"]:
                        g["participants"].append(uid)
                    break
            save_plan(plan)

        client.chat_postMessage(
            channel=dm_channel,
            text=f"✓ Added <@{uid}> to <#{ch['channel_id']}> (Group {gid}) and updated the plan.",
        )
    except Exception as e:
        client.chat_postMessage(channel=dm_channel, text=f"Error: {e}")

def flush_watchers_job():
    """Runs every 24 hours."""
    _do_watcher_flush()
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


# ---------- events: member_joined_channel / member_left_channel ----------

@app.event("member_joined_channel")
def handle_member_joined(event):
    user_id = event.get("user")
    channel_id = event.get("channel")
    if not user_id or not channel_id:
        return

    channels = load_channels()
    ch = next((c for c in channels if c["channel_id"] == channel_id), None)
    if not ch:
        return  # not a bot-managed channel

    group_num = ch["group_id"]
    changed_channels = False
    if user_id not in ch["participants"] and user_id not in ch.get("mentors", []):
        ch["participants"].append(user_id)
        changed_channels = True

    if changed_channels:
        save_channels(channels)

    plan = load_plan()
    if plan:
        for g in plan["groups"]:
            if g["id"] == group_num:
                if user_id not in g["participants"] and user_id not in g["mentors"]:
                    g["participants"].append(user_id)
                    save_plan(plan)
                break


@app.event("member_left_channel")
def handle_member_left(event):
    user_id = event.get("user")
    channel_id = event.get("channel")
    if not user_id or not channel_id:
        return

    channels = load_channels()
    ch = next((c for c in channels if c["channel_id"] == channel_id), None)
    if not ch:
        return

    group_num = ch["group_id"]
    changed_channels = False
    if user_id in ch["participants"]:
        ch["participants"].remove(user_id)
        changed_channels = True
    elif user_id in ch.get("mentors", []):
        ch["mentors"].remove(user_id)
        changed_channels = True

    if changed_channels:
        save_channels(channels)

    plan = load_plan()
    if plan:
        for g in plan["groups"]:
            if g["id"] == group_num:
                if user_id in g["participants"]:
                    g["participants"].remove(user_id)
                    save_plan(plan)
                elif user_id in g["mentors"]:
                    g["mentors"].remove(user_id)
                    save_plan(plan)
                break


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

    if subparts[0].lower() == "run":
        respond(text="Running watcher check now...", response_type="ephemeral")
        threading.Thread(target=flush_watchers_job_once, daemon=True).start()
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
        text=f"Watching <{link}|message> for :{emoji}: reactions.\nYou'll be DM'd every 24 hours with anyone who reacted but isn't in a group yet.",
        response_type="ephemeral",
    )


def handle_mentor_cmd(parts: list[str], respond, client=None, command_channel_id: str | None = None, caller_id: str | None = None):
    mentors = load_mentors()
    subparts = parts[1:]

    if not subparts or subparts[0].lower() == "list":
        if not mentors:
            respond(text="No mentors stored. Use `/professor mentor add @user …`", response_type="ephemeral")
        else:
            respond(text=f"*Mentors ({len(mentors)}):*\n{fmt(mentors)}", response_type="ephemeral")
        return

    sub = subparts[0].lower()

    if sub == "slice":
        if client is None:
            respond(text="Error: client not available.", response_type="ephemeral")
            return
        handle_mentor_slice(subparts[1:], client, respond, command_channel_id=command_channel_id, caller_id=caller_id)
        return

    if sub == "finish":
        if client is None:
            respond(text="Error: client not available.", response_type="ephemeral")
            return
        handle_mentor_finish(client, respond)
        return

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

            # Set channel topic
            try:
                mentor_mentions = " · ".join(f"<@{u}>" for u in g["mentors"])
                topic = f"Mentors: {mentor_mentions} | Ask questions, share what you're building, and connect with your group!"
                client.conversations_setTopic(channel=cid, topic=topic)
            except Exception as te:
                print(f"[topic] Group {g['id']}: {te}")

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

    if sub == "settopic":
        if not channels:
            respond(text="No bot-created channels yet.", response_type="ephemeral")
            return
        plan = load_plan()
        updated, errors = [], []
        for c in channels:
            mentors = c.get("mentors", [])
            if not mentors and plan:
                group = next((g for g in plan["groups"] if g["id"] == c["group_id"]), None)
                if group:
                    mentors = group.get("mentors", [])
            if not mentors:
                errors.append(f"Group {c['group_id']}: no mentors assigned")
                continue
            try:
                mentor_mentions = " · ".join(f"<@{u}>" for u in mentors)
                topic = f"Mentors: {mentor_mentions} | Ask questions, share what you're building, and connect with your group!"
                client.conversations_setTopic(channel=c["channel_id"], topic=topic)
                updated.append(f"<#{c['channel_id']}> (Group {c['group_id']})")
            except Exception as e:
                errors.append(f"Group {c['group_id']}: {e}")
        lines = []
        if updated:
            lines.append("*Topics set on:*\n" + "\n".join(f"• {x}" for x in updated))
        if errors:
            lines.append("*Errors:*\n" + "\n".join(f"• {x}" for x in errors))
        respond(text="\n".join(lines) or "Nothing updated.", response_type="ephemeral")
        return

    if sub == "sync":
        if not channels:
            respond(text="No bot-created channels yet.", response_type="ephemeral")
            return
        plan = load_plan()
        mentors_set = set(load_mentors())
        admin = load_admin()
        total_added = 0
        errors = []
        for c in channels:
            try:
                cursor = None
                live_members = []
                while True:
                    kwargs = {"channel": c["channel_id"], "limit": 200}
                    if cursor:
                        kwargs["cursor"] = cursor
                    resp = client.conversations_members(**kwargs)
                    live_members.extend(resp.get("members", []))
                    cursor = resp.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break

                for uid in live_members:
                    # Add to channels.json
                    if uid not in c["participants"] and uid not in c.get("mentors", []):
                        if uid in mentors_set or uid == admin:
                            c.setdefault("mentors", []).append(uid)
                        else:
                            c["participants"].append(uid)
                        total_added += 1

                    # Add to plan.json
                    if plan:
                        for g in plan["groups"]:
                            if g["id"] == c["group_id"]:
                                if uid not in g["participants"] and uid not in g["mentors"]:
                                    if uid in mentors_set or uid == admin:
                                        g["mentors"].append(uid)
                                    else:
                                        g["participants"].append(uid)
                                break
            except Exception as e:
                errors.append(f"Group {c['group_id']}: {e}")

        save_channels(channels)
        if plan:
            save_plan(plan)

        lines = [f"Synced live channel members → plan. {total_added} new member(s) added."]
        if errors:
            lines.append("Errors:\n" + "\n".join(errors))
        respond(text="\n".join(lines), response_type="ephemeral")
        return

    if sub == "rename":
        if not channels:
            respond(text="No bot-created channels yet.", response_type="ephemeral")
            return
        names = [
            "hideout-一", "hideout-二", "hideout-三", "hideout-四",
            "hideout-五", "hideout-六", "hideout-七", "hideout-八",
            "hideout-九", "hideout-十", "hideout-十一", "hideout-十二",
        ]
        renamed, errors = [], []
        for c in sorted(channels, key=lambda x: x["group_id"]):
            idx = c["group_id"] - 1
            if idx >= len(names):
                errors.append(f"Group {c['group_id']}: no name defined")
                continue
            new_name = names[idx]
            try:
                client.conversations_rename(channel=c["channel_id"], name=new_name)
                c["channel_name"] = new_name
                renamed.append(f"<#{c['channel_id']}> → `{new_name}`")
            except Exception as e:
                errors.append(f"Group {c['group_id']}: {e}")
        save_channels(channels)
        lines = []
        if renamed:
            lines.append("*Renamed:*\n" + "\n".join(renamed))
        if errors:
            lines.append("*Errors:*\n" + "\n".join(errors))
        respond(text="\n".join(lines) or "Nothing renamed.", response_type="ephemeral")
        return

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

            # Update channels.json
            for new_uid in user_ids:
                if new_uid not in ch["participants"] and new_uid not in ch.get("mentors", []):
                    ch["participants"].append(new_uid)
            save_channels(channels)

            # Update plan.json
            plan = load_plan()
            if plan:
                for g in plan["groups"]:
                    if g["id"] == group_num:
                        for new_uid in user_ids:
                            if new_uid not in g["participants"] and new_uid not in g["mentors"]:
                                g["participants"].append(new_uid)
                        break
                save_plan(plan)

            respond(text=f"Added {fmt(user_ids)} to <#{ch['channel_id']}> and updated the plan.", response_type="ephemeral")
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


def handle_mentor_slice(subparts: list[str], client, respond, command_channel_id: str | None = None, caller_id: str | None = None):
    channels = load_channels()
    if not channels:
        respond(text="No channels yet. Run `/professor launch` first.", response_type="ephemeral")
        return

    test_mode = "--test" in subparts
    subparts = [p for p in subparts if p != "--test"]

    emoji = "white_check_mark"
    msg_parts = subparts
    if subparts and subparts[0].startswith(":") and subparts[0].endswith(":"):
        emoji = subparts[0].strip(":")
        msg_parts = subparts[1:]

    custom = " ".join(msg_parts).strip()
    if not custom:
        respond(text="Usage: `/professor mentor slice [--test] [:emoji:] <message>`\nA message is required.", response_type="ephemeral")
        return

    message = f"<!channel>\n{custom}"

    caller_name, caller_avatar = None, None
    if caller_id:
        caller_name, caller_avatar = _get_user_identity(client, caller_id)

    if test_mode:
        if not command_channel_id:
            respond(text="Could not determine current channel for test mode.", response_type="ephemeral")
            return
        target_channels = [c for c in channels if c["channel_id"] == command_channel_id]
        if not target_channels:
            respond(text="This channel isn't a bot-managed group channel. Run this from inside a hideout channel to test.", response_type="ephemeral")
            return
    else:
        target_channels = channels

    slice_records, sent, errors = [], [], []
    for c in target_channels:
        try:
            kwargs = {
                "channel": c["channel_id"],
                "text": message,
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": message}}],
            }
            if caller_name:
                kwargs["username"] = caller_name
            if caller_avatar:
                kwargs["icon_url"] = caller_avatar
            resp = client.chat_postMessage(**kwargs)
            slice_records.append({
                "group_id": c["group_id"],
                "channel_id": c["channel_id"],
                "message_ts": resp["ts"],
                "emoji": emoji,
            })
            sent.append(f"<#{c['channel_id']}>")
        except Exception as e:
            errors.append(f"Group {c['group_id']}: {e}")

    if not test_mode:
        save_slice(slice_records)

    lines = []
    prefix = "🧪 *Test mode* — slice.json not saved. " if test_mode else ""
    if sent:
        lines.append(f"{prefix}Sent to {len(sent)} channel(s)." + ("" if test_mode else " Run `/professor mentor finish` when ready to review who didn't react."))
    if errors:
        lines.append("Errors:\n" + "\n".join(errors))
    respond(text="\n".join(lines), response_type="ephemeral")


def handle_mentor_finish(client, respond):
    admin = load_admin()
    if not admin:
        respond(text="No admin set.", response_type="ephemeral")
        return

    slice_records = load_slice()
    if not slice_records:
        respond(text="No active slice. Run `/professor mentor slice <message>` first.", response_type="ephemeral")
        return

    mentors_set = set(load_mentors())
    channels = load_channels()
    total = 0
    all_blocks = []

    for record in slice_records:
        cid = record["channel_id"]
        gid = record["group_id"]
        emoji = record["emoji"]
        ts = record["message_ts"]

        try:
            resp = client.reactions_get(channel=cid, timestamp=ts, full=True)
            reactors = set()
            for r in resp["message"].get("reactions", []):
                if r["name"] == emoji:
                    reactors = set(r["users"])
                    break
        except Exception:
            reactors = set()

        try:
            cursor, members = None, []
            while True:
                kwargs = {"channel": cid, "limit": 200}
                if cursor:
                    kwargs["cursor"] = cursor
                resp = client.conversations_members(**kwargs)
                members.extend(resp.get("members", []))
                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
        except Exception:
            members = []

        non_reactors = [u for u in members if u not in reactors and u not in mentors_set and u != admin]
        if not non_reactors:
            continue

        total += len(non_reactors)
        all_blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Group {gid}* — <#{cid}> — {len(non_reactors)} didn't react"}})

        for uid in non_reactors:
            all_blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<@{uid}>"},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Remove"},
                    "style": "danger",
                    "value": f"{uid}:{cid}:{gid}",
                    "action_id": "slice_remove",
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Remove member?"},
                        "text": {"type": "mrkdwn", "text": f"Remove <@{uid}> from <#{cid}>?"},
                        "confirm": {"type": "plain_text", "text": "Yes, remove"},
                        "deny": {"type": "plain_text", "text": "Keep"},
                    },
                },
            })

        all_blocks.append({"type": "divider"})

    if total == 0:
        respond(text="Everyone reacted — no one to remove :tada:", response_type="ephemeral")
        return

    header = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Members who didn't react ({total})", "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "Click *Remove* to kick from their channel and update the plan. Mentors are never listed."}]},
        {"type": "divider"},
    ]

    # Slack allows max 50 blocks per message — chunk if needed
    chunk_size = 47
    chunks = [all_blocks[i:i+chunk_size] for i in range(0, len(all_blocks), chunk_size)]
    for i, chunk in enumerate(chunks):
        blocks = (header if i == 0 else []) + chunk
        client.chat_postMessage(channel=admin, text=f"Members who didn't react ({total})", blocks=blocks)

    respond(text=f"DM sent — {total} non-reactor(s) to review.", response_type="ephemeral")


@app.action("slice_readd")
def handle_slice_readd(ack, action, client, body):
    ack()
    uid = body["user"]["id"]
    channel_id, gid_str = action["value"].split(":")
    gid = int(gid_str)

    try:
        client.conversations_invite(channel=channel_id, users=uid)

        channels = load_channels()
        for c in channels:
            if c["channel_id"] == channel_id:
                if uid not in c.get("participants", []) and uid not in c.get("mentors", []):
                    c["participants"].append(uid)
                break
        save_channels(channels)

        plan = load_plan()
        if plan:
            for g in plan["groups"]:
                if g["id"] == gid:
                    if uid not in g["participants"] and uid not in g["mentors"]:
                        g["participants"].append(uid)
                    break
            save_plan(plan)

        admin = load_admin()
        admin_name, admin_avatar = _get_user_identity(client, admin) if admin else ("Professor", None)
        dm_kwargs = {
            "channel": uid,
            "text": "You've been re-added!",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": f"You've been re-added to <#{channel_id}>! Welcome back :)"}}],
            "username": admin_name,
        }
        if admin_avatar:
            dm_kwargs["icon_url"] = admin_avatar
        client.chat_postMessage(**dm_kwargs)

    except Exception as e:
        client.chat_postMessage(channel=uid, text=f"Sorry, there was an error re-adding you: {e}. Please DM the organizer.")


@app.action("slice_remove")
def handle_slice_remove(ack, action, client, body):
    ack()
    uid, channel_id, gid_str = action["value"].split(":")
    gid = int(gid_str)
    dm_channel = body["channel"]["id"]

    try:
        client.conversations_kick(channel=channel_id, user=uid)

        channels = load_channels()
        ch = next((c for c in channels if c["channel_id"] == channel_id), None)
        ch_name = ch["channel_name"] if ch else channel_id
        if ch and uid in ch.get("participants", []):
            ch["participants"].remove(uid)
        save_channels(channels)

        plan = load_plan()
        if plan:
            for g in plan["groups"]:
                if g["id"] == gid:
                    if uid in g.get("participants", []):
                        g["participants"].remove(uid)
                    break
            save_plan(plan)

        # Store removal record
        removed = load_slice_removed()
        removed.append({
            "user_id": uid,
            "group_id": gid,
            "channel_id": channel_id,
            "channel_name": ch_name,
            "removed_at": datetime.now(timezone.utc).isoformat(),
        })
        save_slice_removed(removed)

        # DM the removed user, appearing as the admin
        admin = load_admin()
        admin_name, admin_avatar = _get_user_identity(client, admin) if admin else ("Professor", None)
        dm_kwargs = {
            "channel": uid,
            "text": "You've been removed from your mentor group.",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Hey! I recently sent a message in your mentor group asking everyone to react if they're working on a Fallout project or planning to come to Fallout — since you didn't react, I went ahead and removed you from the group.\n\nIf this was a mistake and you do plan to work on a Fallout project or come to Fallout, press the button below to be re-added!",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Re-add me to my group!"},
                            "style": "primary",
                            "value": f"{channel_id}:{gid}",
                            "action_id": "slice_readd",
                        }
                    ],
                },
            ],
            "username": admin_name,
        }
        if admin_avatar:
            dm_kwargs["icon_url"] = admin_avatar
        client.chat_postMessage(**dm_kwargs)

        client.chat_postMessage(channel=dm_channel, text=f"✓ Removed <@{uid}> from <#{channel_id}> (Group {gid}) and sent them a DM.")
    except Exception as e:
        client.chat_postMessage(channel=dm_channel, text=f"Error removing <@{uid}>: {e}")


def handle_manual_cmd(parts: list[str], respond):
    manual = load_manual_participants()
    subparts = parts[1:]

    if not subparts or subparts[0].lower() == "list":
        if not manual:
            respond(text="No manual participants. Add via `/professor manual add @u` or the website button.", response_type="ephemeral")
        else:
            respond(text=f"*Manual participants ({len(manual)}) — treated as if they reacted:*\n" + "\n".join(f"• {fmt_user(u)}" for u in manual), response_type="ephemeral")
        return

    sub = subparts[0].lower()

    if sub == "add":
        new = parse_mentions(subparts[1:])
        if not new:
            respond(text="Mention at least one user: `/professor manual add @user`", response_type="ephemeral")
            return
        updated = list(dict.fromkeys(manual + new))
        save_manual_participants(updated)
        respond(text=f"Added {len(new)} to manual list. Total: {len(updated)}.", response_type="ephemeral")

    elif sub == "remove":
        rem = parse_mentions(subparts[1:])
        if not rem:
            respond(text="Mention at least one user: `/professor manual remove @user`", response_type="ephemeral")
            return
        updated = [u for u in manual if u not in rem]
        save_manual_participants(updated)
        respond(text=f"Removed {len(rem)}. Remaining: {len(updated)}.", response_type="ephemeral")

    elif sub == "clear":
        save_manual_participants([])
        respond(text="Manual participant list cleared.", response_type="ephemeral")

    else:
        respond(text=f"Unknown manual subcommand `{sub}`.\n\n{USAGE}", response_type="ephemeral")


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


DEFAULT_AUDIT_LINK = "https://hackclub.slack.com/archives/C0ACG0XQWGN/p1777315115802199"
DEFAULT_AUDIT_EMOJI = "fallout-cat"

def handle_audit_cmd(parts: list[str], client, respond):
    if len(parts) < 3:
        message_link, emoji = DEFAULT_AUDIT_LINK, DEFAULT_AUDIT_EMOJI
    else:
        message_link, emoji = parts[1], parts[2].strip(":")
    channel, timestamp = parse_message_link(message_link)
    if not channel or not timestamp:
        respond(text="Could not parse the message link.", response_type="ephemeral")
        return

    excluded = load_exclude_list()
    slack_reactors = get_reactors(client, channel, timestamp, emoji)
    all_participants = list(dict.fromkeys(slack_reactors + load_manual_participants()))
    reactors = [u for u in all_participants if u not in excluded]

    if not reactors:
        respond(text=f"No :{emoji}: reactors found (or all are excluded).", response_type="ephemeral")
        return

    # Build live membership map from Slack (source of truth, not channels.json)
    bot_channels = load_channels()
    live_members: dict[str, int] = {}  # user_id -> group_id
    channel_live_counts: dict[int, int] = {}
    mentors = set(load_mentors())
    admin = load_admin()
    excluded_from_count = mentors | ({admin} if admin else set())

    for c in bot_channels:
        try:
            cursor = None
            members = []
            while True:
                kwargs = {"channel": c["channel_id"], "limit": 200}
                if cursor:
                    kwargs["cursor"] = cursor
                resp = client.conversations_members(**kwargs)
                members.extend(resp.get("members", []))
                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            for uid in members:
                live_members[uid] = c["group_id"]
            channel_live_counts[c["group_id"]] = sum(1 for u in members if u not in excluded_from_count)
        except Exception as e:
            print(f"[audit] members fetch error for group {c['group_id']}: {e}")

    missing, placed = [], []
    for uid in reactors:
        gid = live_members.get(uid)
        if gid:
            placed.append((uid, gid))
        else:
            missing.append(uid)

    lines = [f"*Audit: :{emoji}: reactors ({len(reactors)} total, {len(excluded)} excluded)*"]
    lines.append(f"✓ In a channel: {len(placed)}  |  ✗ Missing: {len(missing)}")

    if missing:
        lines.append(f"\n*Not in any group channel ({len(missing)}):*")
        lines.append("\n".join(f"• {fmt_user(u)}" for u in missing))

    if channel_live_counts:
        lines.append("\n*Participants per channel (live, excl. mentors & admin):*")
        for c in sorted(bot_channels, key=lambda x: x["group_id"]):
            count = channel_live_counts.get(c["group_id"], "?")
            lines.append(f"• <#{c['channel_id']}> (Group {c['group_id']}): {count} participants")

    respond(text="\n".join(lines), response_type="ephemeral")


def _get_user_identity(client, user_id: str) -> tuple[str, str | None]:
    """Returns (display_name, avatar_url) for a user."""
    try:
        resp = client.users_info(user=user_id)
        profile = resp["user"]["profile"]
        name = profile.get("display_name") or profile.get("real_name") or "Professor"
        avatar = profile.get("image_192") or profile.get("image_72")
        return name, avatar
    except Exception:
        return "Professor", None

def handle_delete_cmd(parts: list[str], client, respond):
    if len(parts) < 2:
        respond(text="Usage: `/professor delete <message_link>` or `/professor delete slice`", response_type="ephemeral")
        return

    if parts[1].lower() == "slice":
        slice_records = load_slice()
        if not slice_records:
            respond(text="No slice messages to delete.", response_type="ephemeral")
            return
        deleted, errors = [], []
        for r in slice_records:
            try:
                client.chat_delete(channel=r["channel_id"], ts=r["message_ts"])
                deleted.append(f"<#{r['channel_id']}>")
            except Exception as e:
                errors.append(f"<#{r['channel_id']}>: {e}")
        save_slice([])
        lines = []
        if deleted:
            lines.append(f"Deleted {len(deleted)} slice message(s): " + "  ".join(deleted))
        if errors:
            lines.append("Errors:\n" + "\n".join(errors))
        respond(text="\n".join(lines), response_type="ephemeral")
        return

    channel, timestamp = parse_message_link(parts[1])
    if not channel or not timestamp:
        respond(text="Could not parse the message link. Copy it via *Copy link* in Slack.", response_type="ephemeral")
        return
    try:
        client.chat_delete(channel=channel, ts=timestamp)
        respond(text="Message deleted.", response_type="ephemeral")
    except Exception as e:
        respond(text=f"Error: {e}", response_type="ephemeral")


def handle_announce_cmd(parts: list[str], client, respond, ping: str | None = None, channel_id: str | None = None, caller_id: str | None = None):
    message = " ".join(parts[1:]).strip()
    if not message and ping not in ("here", "channel"):
        respond(text=f"Usage: `/professor announce <message>`", response_type="ephemeral")
        return

    if ping == "here":
        full_message = f"<!here> {message}".strip()
    elif ping == "channel":
        full_message = f"<!channel> {message}".strip()
    else:
        full_message = message

    # For here/channel pings, impersonate the caller's name and avatar
    caller_name, caller_avatar = None, None
    if ping in ("here", "channel") and caller_id:
        caller_name, caller_avatar = _get_user_identity(client, caller_id)

    if ping in ("here", "channel"):
        # Send only to the channel where the command was run
        if not channel_id:
            respond(text="Could not determine the current channel.", response_type="ephemeral")
            return
        try:
            kwargs = dict(
                channel=channel_id,
                text=full_message,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": full_message}}],
                link_names=True,
            )
            if caller_name:
                kwargs["username"] = caller_name
            if caller_avatar:
                kwargs["icon_url"] = caller_avatar
            client.chat_postMessage(**kwargs)
            respond(text=f"Sent to <#{channel_id}>.", response_type="ephemeral")
        except Exception as e:
            respond(text=f"Error: {e}", response_type="ephemeral")
    else:
        # announce goes to all channels
        channels = load_channels()
        if not channels:
            respond(text="No bot-created channels yet. Run `/professor launch` first.", response_type="ephemeral")
            return
        sent, errors = [], []
        for c in channels:
            try:
                client.chat_postMessage(channel=c["channel_id"], text=full_message)
                sent.append(f"<#{c['channel_id']}>")
            except Exception as e:
                errors.append(f"<#{c['channel_id']}>: {e}")
        lines = []
        if sent:
            lines.append(f"Sent to {len(sent)} channel(s): " + "  ".join(sent))
        if errors:
            lines.append("Failed:\n" + "\n".join(errors))
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
            # Mentors may use here/channel only in their own assigned group channel
            if mode in ("here", "channel"):
                invoke_channel_id = command.get("channel_id")
                channels = load_channels()
                mentor_channel = next(
                    (c for c in channels if c["channel_id"] == invoke_channel_id and caller_id in c.get("mentors", [])),
                    None,
                )
                if not mentor_channel:
                    respond(text="You can only use `/professor here` and `/professor channel` in your own group channel.", response_type="ephemeral")
                    return
            else:
                respond(text=f"Only the admin (<@{admin}>) can use this bot.", response_type="ephemeral")
                return

    try:
        if mode == "admin":
            handle_admin_cmd(parts, caller_id, respond)
        elif mode == "watch":
            handle_watch_cmd(parts, respond)
        elif mode == "mentor":
            handle_mentor_cmd(parts, respond, client=client, command_channel_id=command.get("channel_id"), caller_id=caller_id)
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
        elif mode == "delete":
            handle_delete_cmd(parts, client, respond)
        elif mode in ("announce", "here", "channel"):
            handle_announce_cmd(parts, client, respond, ping=mode if mode in ("here", "channel") else None, channel_id=command.get("channel_id"), caller_id=caller_id)
        elif mode == "manual":
            handle_manual_cmd(parts, respond)
        elif mode == "exclude":
            handle_exclude_cmd(parts, respond)
        elif mode in ("list", "random", "groups"):
            handle_reaction_cmd(mode, parts, client, respond)
        else:
            respond(text=f"Unknown command `{mode}`.\n\n{USAGE}", response_type="ephemeral")
    except Exception as e:
        respond(text=f"Error: {e}", response_type="ephemeral")


if __name__ == "__main__":
    threading.Thread(target=start_flask, daemon=True).start()
    threading.Timer(86400, flush_watchers_job).start()
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
