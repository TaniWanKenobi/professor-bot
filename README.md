# professor-bot

Automates Hack Club mentorship program logistics: collects emoji-reaction signups, splits participants into balanced groups, assigns mentors, creates private group channels with canvases, and watches for new signups.

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
      usage_hint: "[plan|assign|launch|watch|mentor|channels|exclude|admin|list|random|groups] ..."
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
  socket_mode_enabled: true
  token_rotation_enabled: false
```

**`should_escape: true`** is required — without it, Slack sends `@Tanuki` as plain text and the bot can't parse it as a user mention.

---

## plan.json formats

The bot accepts two formats. You can paste either directly into `plan.json` on Nest.

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

**Hand-crafted format** (easier to write manually — what the handoff doc uses):

```json
{
  "groups": {
    "1": {"participants": ["U123", "U456"], "mentors": []},
    "2": {"participants": ["U789", "U012"], "mentors": []}
  }
}
```

Both are loaded identically. The bot normalizes the dict format to a list internally.

---

## Typical workflow

```text
1. /professor admin set @you
   /professor mentor set @mentor1 @mentor2 @mentor3 …
   /professor exclude add @organizer1 @organizer2 …

2. /professor watch <signup_message_link> :fallout-cat:
   → you'll be DM'd every 5 min with new reactors + whether they're already in a group

3. /professor plan 12 <signup_message_link> :fallout-cat:
   → splits reactors (minus excluded) into 12 random groups
   → OR paste a hand-crafted plan.json directly on Nest for pre-balanced groups

4. /professor plan show
   → shows all groups with real display names

5. /professor assign 1 @mentor1 @mentor2
   /professor assign 2 @mentor3 @mentor4
   … repeat for each group (2 mentors preferred, 3 if needed)

6. /professor launch gc
   → creates #gc-group-1 … #gc-group-12
   → invites admin first, then participants + mentors
   → creates a canvas in each channel with group roster
```

---

## Commands

### Admin

Only the admin can run any command. `/professor admin show` is public so anyone can see who to contact.

| Command | What it does |
| --- | --- |
| `/professor admin set @you` | Set the admin (first-time only, or current admin) |
| `/professor admin show` | Show who the admin is |
| `/professor admin clear` | Remove the admin |

To become channel manager in a group channel: open it → click your name → **Make channel manager**.

---

### Reaction watcher

Watch a signup message and get DM'd every 5 minutes with new reactors. Each DM tells you whether the reactor is already in a group channel or not — useful for monitoring late signups mid-program.

New reactors are deduplicated: you're only notified once per person per watcher.

| Command | What it does |
| --- | --- |
| `/professor watch <link> <emoji>` | Start watching a message |
| `/professor watch list` | Show active watchers (seen count, pending count) |
| `/professor watch remove <link> <emoji>` | Stop watching one message |
| `/professor watch clear` | Remove all watchers |

**Example DM you'll receive:**

```text
New :fallout-cat: reactions on watched message:
• @alice — ✗ not in any group yet
• @bob — ✓ already in Group 3
```

---

### Plan

Fetches emoji reactors, removes excluded users, splits into N random groups, and saves to `plan.json`.

| Command | What it does |
| --- | --- |
| `/professor plan <N> <link> <emoji>` | Create N random groups from reactors |
| `/professor plan show` | Show plan with real display names (fetches from Slack) |
| `/professor plan clear` | Discard the current plan |

**Loading a hand-crafted plan:** SSH into Nest, `nano plan.json`, paste your JSON in the dict format above, save. The bot loads it automatically on next command.

---

### Assign mentors

Manually assign mentors to each group. Re-running on the same group overwrites only that group — other groups are untouched.

| Command | What it does |
| --- | --- |
| `/professor assign <group#> @mentor1 @mentor2` | Set mentors for a group (warns if >3) |
| `/professor assign show` | Show full plan with current assignments |
| `/professor assign clear` | Clear all mentor assignments |

---

### Launch

Creates a private Slack channel for every group that has mentors assigned. Admin is invited first (for channel manager promotion), then participants and mentors. A **canvas** is created in each channel with the group roster.

```text
/professor launch [prefix]
```

Default prefix is `gc` → channels named `#gc-group-1`, `#gc-group-2`, etc.

All groups must have mentors assigned before launch will run.

---

### Channel management

| Command | What it does |
| --- | --- |
| `/professor channels list` | List all bot-created channels |
| `/professor channels add @u <group#>` | Add someone to a group's channel |
| `/professor channels kick @u <group#>` | Remove someone from a group's channel |
| `/professor channels archive` | Archive all bot-created channels |

**Adding a mentor after channels are created:**

```text
/professor assign 3 @newmentor        ← update the plan
/professor channels add @newmentor 3  ← add to the channel
```

---

### Mentor list

Adding a mentor automatically adds them to the exclude list so they don't appear as participants when running `/professor plan`.

| Command | What it does |
| --- | --- |
| `/professor mentor set @u1 @u2 …` | Replace entire mentor list (also excludes all) |
| `/professor mentor add @u …` | Add mentors (also excludes them) |
| `/professor mentor remove @u …` | Remove from mentor list |
| `/professor mentor list` | Show stored mentors |
| `/professor mentor clear` | Clear mentor list |

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
| `/professor groups <N> <link> <emoji>` | Preview N groups without saving a plan |

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
