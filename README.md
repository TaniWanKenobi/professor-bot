# professor-bot

Automates Hack Club mentorship program logistics: collects emoji-reaction signups, splits participants into balanced groups, assigns mentors, creates private group channels with canvases and topics, watches for new signups, and keeps everything in sync.

---

## Slack app manifest

```yaml
display_information:
  name: Professor
  description: Mentorship group channel manager

features:
  bot_user:
    display_name: Professor
    always_online: true
  slash_commands:
    - command: /professor
      description: Manage mentorship group channels
      usage_hint: "[plan|assign|launch|watch|audit|announce|mentor|channels|exclude|admin|list|random|groups] ..."
      should_escape: true

oauth_config:
  scopes:
    bot:
      - commands
      - reactions:read
      - channels:join
      - groups:write
      - channels:manage
      - chat:write
      - im:write
      - users:read
      - canvases:write

settings:
  event_subscriptions:
    bot_events:
      - reaction_added
      - member_joined_channel
      - member_left_channel
  interactivity:
    is_enabled: true
  socket_mode_enabled: true
  token_rotation_enabled: false
```

**`should_escape: true`** is required — without it, Slack sends `@mentions` as plain text and the bot can't parse them as user IDs.

---

## plan.json formats

The bot accepts two formats. Paste either directly into `plan.json` on Nest.

**Bot-generated format** (what `/professor plan` produces):

```json
{
  "created_at": "2024-...",
  "source": {"link": "https://...", "emoji": "fallout-cat"},
  "groups": [
    {"id": 1, "participants": ["U123", "U456"], "mentors": []},
    {"id": 2, "participants": ["U789", "U012"], "mentors": []}
  ]
}
```

**Hand-crafted format** (easier to write manually):

```json
{
  "groups": {
    "1": {"participants": ["U123", "U456"], "mentors": []},
    "2": {"participants": ["U789", "U012"], "mentors": []}
  }
}
```

Both are loaded identically.

---

## Typical workflow

```text
1. /professor admin set @you
   /professor mentor set @mentor1 @mentor2 …
   /professor watch <signup_link> :fallout-cat:

2. /professor plan 12 <signup_link> :fallout-cat:
   — OR paste a hand-crafted plan.json on Nest

3. /professor plan show

4. /professor assign 1 @mentor1 @mentor2
   … repeat for each group

5. /professor launch gc
   → creates channels, sets topics, creates canvases

6. /professor channels addadmin
   /professor channels settopic
   /professor channels rename

7. /professor audit
   → check who reacted but isn't placed yet
```

---

## Commands

### Admin

Only the admin can run any command. `admin show` is public.

| Command | What it does |
| --- | --- |
| `/professor admin set @you` | Set the admin |
| `/professor admin show` | Show who the admin is (public) |
| `/professor admin clear` | Remove the admin |

---

### Group channel workflow

#### Plan

| Command | What it does |
| --- | --- |
| `/professor plan <N> <link> <emoji>` | Split reactors (minus excluded) into N groups |
| `/professor plan show` | Show plan — each user as `@mention (USERID)` |
| `/professor plan clear` | Discard the current plan |

#### Assign mentors

| Command | What it does |
| --- | --- |
| `/professor assign <group#> @mentor1 @mentor2` | Set mentors for a group (warns if >3) |
| `/professor assign show` | Show full plan with assignments |
| `/professor assign clear` | Clear all mentor assignments |

#### Launch

```text
/professor launch [prefix]
```

Creates a private channel per group, sets the channel topic to mentor names, creates a canvas with the group roster, and invites everyone. Default prefix is `gc`.

---

### Reaction watcher

Watches a signup message and DMs you every 24 hours with anyone who reacted but isn't in a group yet. Each person appears with a dropdown showing all groups sorted by fewest mentees — select a group and they're instantly added to the channel and plan. No commands needed.

| Command | What it does |
| --- | --- |
| `/professor watch <link> <emoji>` | Start watching a message |
| `/professor watch list` | Show active watchers |
| `/professor watch run` | Manually trigger the 24h check right now |
| `/professor watch remove <link> <emoji>` | Stop watching a message |
| `/professor watch clear` | Remove all watchers |

---

### Audit

Checks who reacted to the signup message but isn't in any group channel yet. Uses live Slack membership (not cached data), so manual additions are included.

| Command | What it does |
| --- | --- |
| `/professor audit` | Audit the default signup message |
| `/professor audit <link> <emoji>` | Audit a custom message |

Also shows participant counts per channel (excluding mentors and admin).

---

### Announce

Post a message to every bot-created channel at once.

```text
/professor announce <message>
```

---

### Mentor list

Adding a mentor automatically adds them to the exclude list so they don't appear as participants.

| Command | What it does |
| --- | --- |
| `/professor mentor set @u1 @u2 …` | Replace entire mentor list |
| `/professor mentor add @u …` | Add mentors |
| `/professor mentor remove @u …` | Remove from mentor list |
| `/professor mentor list` | Show stored mentors |
| `/professor mentor clear` | Clear mentor list |

---

### Channel management

| Command | What it does |
| --- | --- |
| `/professor channels list` | List all bot-created channels |
| `/professor channels add @u <group#>` | Add someone to a channel — updates plan too |
| `/professor channels kick @u <group#>` | Remove someone from a channel |
| `/professor channels sync` | Pull live Slack members into plan (fixes people added manually) |
| `/professor channels addadmin` | Add admin to all channels |
| `/professor channels settopic` | Set mentor names as topic on all channels |
| `/professor channels rename` | Rename channels to Hideout-一 … Hideout-十二 |
| `/professor channels archive` | Archive all bot-created channels |

**Plan stays in sync automatically** — `member_joined_channel` and `member_left_channel` events update the plan whenever anyone joins or leaves a bot-created channel. Use `/professor channels sync` to catch anyone added before this was set up.

---

### Exclude list

Excluded users are silently filtered from all `plan`, `list`, `random`, and `groups` commands.

| Command | What it does |
| --- | --- |
| `/professor exclude` | Show excluded users |
| `/professor exclude add @u …` | Add to exclude list |
| `/professor exclude remove @u …` | Remove from exclude list |
| `/professor exclude clear` | Clear the exclude list |

Inline exclusion (also saves to persist list):

```text
/professor groups 3 <link> thumbsup --exclude @alice @bob
```

---

### Quick lookups

Read-only, no state saved.

| Command | What it does |
| --- | --- |
| `/professor list <link> <emoji>` | List all reactors |
| `/professor random <link> <emoji>` | List reactors in random order |
| `/professor groups <N> <link> <emoji>` | Preview N groups without saving |

All support `--exclude @u …` at the end.

---

## Stored files

| File | Contents |
| --- | --- |
| `.env` | Slack tokens — never commit |
| `admin.json` | Admin user ID |
| `mentors.json` | Mentor user IDs |
| `exclude_list.json` | Excluded user IDs |
| `plan.json` | Current group plan — accepts both formats |
| `channels.json` | All channels created by `/professor launch` |
| `watchers.json` | Active reaction watchers |
