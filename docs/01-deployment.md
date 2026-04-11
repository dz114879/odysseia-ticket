# Deployment Guide

This guide walks you through creating a Discord application, configuring the bot, and running it on your server.

## 1. Discord Developer Portal Setup

### 1.1 Create an Application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application**, give it a name (e.g. "Odysseia Ticket"), and confirm.
3. Note the **Application ID** on the General Information page — you'll need it for `DISCORD_APP_ID` in your `.env` file.

### 1.2 Create a Bot User

1. Navigate to the **Bot** tab in the left sidebar.
2. Under the Token section, click **Reset Token** and copy the token — you'll need it for `DISCORD_BOT_TOKEN`. **Keep this secret.**
3. (Optional) Disable **Public Bot** if you don't want others to invite it.

### 1.3 Enable Privileged Gateway Intents

Still on the **Bot** tab, scroll down to **Privileged Gateway Intents** and enable:

| Intent | Required | Why |
|--------|----------|-----|
| **Server Members Intent** | Yes | The bot needs to read member info for permission overwrites and staff management |
| **Message Content Intent** | Yes | The bot needs to read message content for ticket drafts and snapshot history |

> **Note:** Presence Intent is **not** required — leave it off.

### 1.4 Generate an Invite Link

1. Navigate to the **OAuth2** tab.
2. Under **OAuth2 URL Generator**, select these scopes:
   - `bot`
   - `applications.commands`
3. In the **Bot Permissions** section below, select:

| Permission | Why |
|------------|-----|
| **Manage Channels** | Create/delete ticket channels, modify channel permissions |
| **Manage Messages** | Pin messages, manage ticket channel content |
| **Send Messages** | Respond in ticket channels |
| **Embed Links** | Send rich embeds for panels, ticket info, and transcripts |
| **Attach Files** | Send HTML transcript files during archival |
| **Read Message History** | Read ticket conversation history for snapshots and transcripts |
| **View Channels** | Access ticket channels and categories |
| **Use Application Commands** | Register and respond to slash commands |

4. Copy the generated URL and open it in your browser to invite the bot to your server.

## 2. Server Preparation

Before running the bot, make sure your Discord server has:

- A **category** where ticket channels will be created.
- A **text channel** for staff logs (the bot posts ticket lifecycle events here).
- A **text channel** for archives (closed ticket transcripts are sent here).
- A **role** for staff/admins who will handle tickets.

> You'll configure all of these via the `/ticket setup` command after the bot is online.

## 3. Install & Configure

### 3.1 Prerequisites

- **Python 3.11+**
- **[uv](https://github.com/astral-sh/uv)** — Python package manager

### 3.2 Clone & Install

```bash
git clone <repo-url>
cd odysseia-ticket
uv sync
```

### 3.3 Environment Variables

Copy the example file and fill in the values:

```bash
cp .env.example .env
```

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_BOT_TOKEN` | Bot token from step 1.2 | *(required)* |
| `DISCORD_APP_ID` | Application ID from step 1.1 | *(required)* |
| `BOT_PREFIX` | Legacy command prefix | `!` |
| `SQLITE_PATH` | Path to the SQLite database file | `data/ticket_bot.sqlite3` |
| `LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `LOG_FILE` | Path to the log file | `logs/ticket-bot.log` |
| `SCHEDULER_INTERVAL_SECONDS` | Interval for periodic tasks (queue sweep, etc.) | `30` |
| `AUTO_SYNC_COMMANDS` | Sync slash commands on startup (`true`/`false`) | `false` |

> **Tip:** Set `AUTO_SYNC_COMMANDS=true` on first run so the bot registers its slash commands with Discord. You can set it back to `false` afterwards to speed up startup — commands only need to be re-synced when they change.

## 4. Run the Bot

```bash
uv run python bot.py
```

The bot will:
1. Load environment settings from `.env`
2. Initialize the SQLite database and run any pending migrations
3. Start runtime infrastructure (cache, locks, scheduler)
4. Load all cog modules and restore persistent panel views
5. Connect to Discord

## 5. First-Time Setup

Once the bot is online in your server:

1. Run `/ticket setup` (requires **Administrator** permission).
2. The setup wizard will prompt you to configure:
   - **Log channel** — where ticket lifecycle events are posted
   - **Archive channel** — where closed ticket transcripts are sent
   - **Ticket category** — the channel category for new tickets
   - **Admin role** — the role that grants staff access to tickets
3. After setup, use `/ticket panel create` in a public channel to post the ticket creation panel.

Your bot is now ready to accept tickets.
