# nuclear — Slack Group Channel Creator

A Slack bot that takes emoji reactors from a message, splits them into groups, lets you assign mentors, and creates a private channel for each group.

---

## Setup

### 1. Environment variables

Create a `.env` file in the project root:

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

- **`SLACK_BOT_TOKEN`** — Bot OAuth token from your Slack app's *OAuth & Permissions* page.
- **`SLACK_APP_TOKEN`** — App-level token with `connections:write` scope (*Basic Information → App-Level Tokens*). Required for Socket Mode.

### 2. Slack app permissions

| Scope | Why |
| --- | --- |
| `commands` | Register the `/professor` slash command |
| `reactions:read` | Read emoji reactions on messages |
| `channels:join` | Join public channels to read reactions |
| `groups:write` | Create private channels and invite members |
| `channels:manage` | Archive channels |

Enable **Socket Mode** in your Slack app settings.

### 3. Install and run

```bash
uv sync
python app.py
```

---

## Typical workflow

```text
1. /professor mentor set @alice @bob @carol @dan   ← set your mentor pool
2. /professor plan 8 <message_link> :fallout-cat:  ← split reactors into 8 groups
3. /professor assign 1 @alice @bob                 ← pick 2 mentors per group
   /professor assign 2 @carol @dan
   ...
4. /professor launch gc                            ← creates #gc-group-1 … #gc-group-8
```

Participants are everyone who reacted with the given emoji, minus anyone on the [exclude list](#exclude-list).

**Mentor count:** prefer 2 per group, 3 is fine when needed. The bot warns you if you assign more than 3.

---

## Commands

### Reaction lookups (quick, no state saved)

```text
/professor list   <link> <emoji> [--exclude @u …]
/professor random <link> <emoji> [--exclude @u …]
/professor groups <N> <link> <emoji> [--exclude @u …]
```

These are read-only previews. `--exclude` here *does* persist the excluded users for future commands.

---

### Group channel workflow

#### `/professor plan <N> <link> <emoji>`

Fetch reactors, remove excluded users, split into N random groups, and save the plan.

```text
/professor plan 8 https://hackclub.slack.com/archives/C0ACG0XQWGN/p177… fallout-cat
```

#### `/professor plan show`

Show the current saved plan with participants and any mentor assignments.

#### `/professor plan clear`

Discard the current plan.

#### `/professor assign <group#> @mentor1 @mentor2`

Assign mentors to a specific group. You can run this as many times as needed — it only touches that one group, so adding or swapping a mentor never re-randomizes anyone else.

```text
/professor assign 3 @alice @bob
/professor assign 3 @alice @bob @carol   ← update to 3 mentors if needed
```

#### `/professor assign show`

Show the full plan including current mentor assignments.

#### `/professor assign clear`

Clear all mentor assignments without touching participant groups.

#### `/professor launch [prefix]`

Create a private Slack channel for every group that has mentors assigned, and invite all participants + mentors. Channel names default to `gc-group-1`, `gc-group-2`, etc. Pass a custom prefix:

```text
/professor launch cohort2
```

Creates `#cohort2-group-1`, `#cohort2-group-2`, …

---

### Mentor list

The mentor list is a stored reference — it doesn't auto-assign anyone. Use it to keep track of who your mentors are, then manually assign them to groups with `/professor assign`.

```text
/professor mentor list
/professor mentor add @alice @bob
/professor mentor remove @alice
/professor mentor set @alice @bob @carol @dan   ← replaces the entire list
/professor mentor clear
```

---

### Channel management

Once channels are created via `/professor launch`, you can manage them:

```text
/professor channels list                   ← show all bot-created channels
/professor channels add @user <group#>     ← add someone to a group's channel
/professor channels kick @user <group#>    ← remove someone from a group's channel
/professor channels archive                ← archive all bot-created channels
```

**Adding a mentor after launch:** just run `/professor assign <group#> @newmentor` to update the plan, then `/professor channels add @newmentor <group#>` to actually add them to the channel. No other groups are affected.

---

### Exclude list

Users on the exclude list are silently filtered from all `plan`, `list`, `random`, and `groups` commands.

```text
/professor exclude                  ← show excluded users
/professor exclude add @alice @bob
/professor exclude remove @alice
/professor exclude clear
```

Using `--exclude @user` inline on any reaction command also adds those users to the persistent list.

---

## Stored files

| File | Contents |
| --- | --- |
| `exclude_list.json` | Array of excluded Slack user IDs |
| `mentors.json` | Array of mentor Slack user IDs |
| `plan.json` | Current group plan (participants + mentor assignments) |
| `channels.json` | Log of every channel created by `/professor launch` |

All files persist across bot restarts.
