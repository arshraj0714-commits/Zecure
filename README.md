# SecurityBot Enterprise

> Enterprise-grade Discord security bot inspired by **SecurityBot**, **Wick**, and **AuthGG**.
> Built in Python with `discord.py` 2.3+. Designed for production deployment on Railway, Docker, or any container platform.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![discord.py](https://img.shields.io/badge/discord.py-2.3+-7289DA.svg)](https://discordpy.readthedocs.io)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🛡️ Features

### Anti-Nuke Protection
Comprehensive protection against server-destruction attacks:
- **Channel** create/delete (mass)
- **Role** create/delete (mass)
- **Webhook** creation spam
- **Emoji** create/delete (mass)
- **Sticker** create/delete (mass)
- **Bot additions** (zero-tolerance for unauthorized bots)
- **Member** kick/ban/timeout (mass)
- **Permission override** changes
- **Server update** (name, icon)
- **Vanity URL** changes
- **Prune** triggers
- **Reversal** of destructive actions where possible
- **Configurable thresholds** per guild
- **Punishments**: strip roles / kick / ban / quarantine

### AutoMod
Message-level content filtering:
- **Bad words** (built-in + custom per guild)
- **Discord invites** detection
- **Scam links** (known patterns)
- **Phishing URLs** (curated domain list)
- **IP loggers** (Grabify, iplogger, etc.)
- **NSFW keywords**
- **Caps spam**
- **Excessive emojis**
- **Mention spam**
- **Excessive attachments**
- **Duplicate messages**
- **Flood detection**
- **Ghost ping detection**
- Configurable per-guild enable/disable + punishment

### AntiRaid
Detects and mitigates coordinated raids:
- **Join raids** (mass joins in short window)
- **Leave raids** (mass leaves)
- **DM raids** (user-reported)
- **Verification lockdown**
- **Auto-lockdown** on raid detection
- **Quarantine** suspicious users
- **Smart detection** — only triggers when there's clear malicious intent (e.g., majority of joiners are new accounts)
- **Auto-clear** raid state after 15 minutes
- **Owner notification** on every raid

### AntiSpam
Behavioral spam detection:
- Message rate limiting
- Mention spam
- Link spam
- Attachment spam
- Repetitive content
- Sticker spam
- **Progressive punishments**: warn → timeout → kick → ban

### AntiTamper
Protects bot configuration and server integrity:
- Detects dangerous role permission grants (Administrator, Ban, Manage Roles, etc.)
- Blocks Discord token sharing (24h timeout)
- Blocks self-bot promotion
- Protects specified roles from modification
- Reverts unauthorized changes

### Moderation Commands
- `!ban`, `!unban`, `!kick`, `!mute` (timeout), `!unmute`, `!warn`
- `!warnings`, `!clearwarnings`, `!history`
- `!purge` / `!clear`
- `!lock`, `!unlock`, `!slowmode`
- `!userinfo`, `!serverinfo`
- `!incidents`, `!resolveincident`

### Verification System
- **Button-based** verification (click to verify)
- **Math captcha** verification
- **Account age** requirement
- **Auto-role** assignment on verify
- **Failed attempt** tracking (auto-quarantine after 5 failures)

### Backup & Restore
- Full server backups (channels, roles, settings)
- Per-guild backup history
- Restore channels, roles, or both
- Backups stored as JSON

### Logging & Audit
- Configurable log channels:
  - **Mod log** — moderation actions
  - **Server log** — channel/role changes
  - **Member log** — joins/leaves
  - **Voice log** — voice activity
  - **Security log** — security alerts
  - **Audit log** — all logged actions
- Stored in SQLite database for queryability

### Analytics
- **Security score** (0-100) with detailed factor breakdown
- Server statistics (members, channels, roles, boosts)
- Moderation activity metrics (7d / 30d)
- Top members by various criteria
- Score history tracking

### Scheduled Tasks
- One-time tasks (`!schedule backup in 2h`)
- Recurring tasks (daily, cron)
- Actions: backup, lock, unlock, announce
- Enable/disable tasks

### Whitelist System
- Whitelist **users**, **roles**, or **channels**
- Whitelisted entities bypass all security checks
- Per-guild management with reason tracking

### Role-Based Permissions
- **Owner** (env `OWNER_ID`) — full access, bypasses everything
- **Guild owner** — full access to their guild
- **Admin roles** (set via `!addadminrole`) — admin command access
- **Whitelisted users** — bypass security checks
- **Discord Administrator permission** — admin command access

### Multi-Language Support
Built-in locales:
- 🇬🇧 English (`en`)
- 🇪🇸 Spanish (`es`)
- 🇫🇷 French (`fr`)
- 🇩🇪 German (`de`)
- 🇵🇹 Portuguese (`pt`)
- 🇮🇳 Hindi (`hi`)

Set per-guild with `!setlanguage <code>`.

### Beautiful Embeds
- Consistent color system (success/warning/danger/info/security)
- Severity-coded alerts
- Rich user embeds with avatars and timestamps
- Brand footer on all embeds
- Icon-decorated titles

### Pluggable Architecture
- Database layer abstracts storage (swap SQLite → PostgreSQL by changing `DATABASE_URL`)
- Web API stub (`WEB_API_ENABLED`, `WEB_API_PORT`) for future dashboard integrations

### High Performance
- Async SQLite with WAL mode for concurrent reads
- Connection pooling via `aiosqlite`
- Sliding-window event tracking (deque-based, O(1) pruning)
- Caching for whitelist lookups
- Configurable worker count

### Complete Error Handling
- Global command error handler
- Slash command error handler
- Permission-aware error messages
- Graceful degradation on missing permissions

### Owner Notifications
**Every** critical security event is DM'd to the configured owner (`OWNER_ID=1498693593701945374`):
- Bot startup
- Anti-Nuke triggers
- Anti-Raid triggers
- AntiTamper violations
- Guild join/leave
- Manual raid mode toggles
- Quarantine actions

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- A Discord bot token ([create one here](https://discord.com/developers/applications))
- Bot must have all privileged intents enabled (Presence, Server Members, Message Content)

### 1. Clone & Configure

```bash
git clone <your-repo-url>
cd securitybot
cp .env.example .env
# Edit .env with your token and owner ID
```

### 2. Run with Docker

```bash
docker compose up -d
```

### 3. Run Locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m bot.main
```

### 4. Invite the Bot

Use this URL (already configured with this bot's client ID):

```
https://discord.com/api/oauth2/authorize?client_id=1518963211586764851&permissions=8&scope=bot%20applications.commands
```

**Required permissions:** Administrator (permission `8`) is recommended for full functionality.

---

## 🚢 Railway Deployment (Recommended)

This project includes a `nixpacks.toml` pre-configured for [Railway](https://railway.app).

### Step-by-step

1. **Push this repo to GitHub** (Railway reads from GitHub).

2. **Create a new Railway project** → "Deploy from GitHub repo" → select your repo.

3. **Add environment variables** (Railway dashboard → Variables tab):
   - `DISCORD_TOKEN` = `YOUR_TOKEN_HERE`
   - `OWNER_ID` = `1498693593701945374`
   - (optional) `LOG_LEVEL` = `INFO` (or `DEBUG` for verbose logs)
   - (optional) `DEFAULT_PREFIX` = `!`

4. **Attach a persistent volume** so SQLite data survives redeploys:
   - Railway dashboard → Settings → Volumes → Add Volume
   - Mount path: `/app/data`
   - This preserves `security_bot.db`, logs, and backups across restarts.

5. **Deploy** — Railway will detect `nixpacks.toml`, install Python 3.11, install requirements, and start `python -m bot.main` automatically.

6. **Verify** — check the Deployments tab for "Active" status, and the Logs tab for:
   ```
   INFO  │ securitybot.bot │ Running setup_hook...
   INFO  │ securitybot.bot │ Logged in as <your bot> (ID: 1518963211586764851)
   INFO  │ securitybot.bot │ Connected to N guilds
   ```
   The owner (`1498693593701945374`) will also receive a DM confirming the bot is online.

### Railway gotchas
- The bot uses **all privileged intents** (Presence, Server Members, Message Content). Enable these in the [Discord Developer Portal](https://discord.com/developers/applications/1518963211586764851/bot) under "Privileged Gateway Intents".
- If you skip the volume mount, the bot still works but resets DB on every redeploy.
- Railway auto-sleeps free-tier services after inactivity — upgrade to a paid plan for 24/7 uptime.


---

## ⚙️ Configuration

All configuration is via environment variables (see `.env.example`).

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_TOKEN` | (required) | Bot token from Discord Developer Portal |
| `OWNER_ID` | `1498693593701945374` | Bot owner's Discord user ID (receives all critical alerts) |
| `DEFAULT_PREFIX` | `!` | Default command prefix (overridable per guild) |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/security_bot.db` | Database URL |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `DEFAULT_LANGUAGE` | `en` | Default language for new guilds |
| `DRY_RUN` | `false` | If true, disables destructive actions (testing) |

### Per-Guild Configuration

Once the bot is running, configure each guild:

```bash
!setprefix ?              # Change prefix
!setlanguage es           # Set language
!addadminrole @Mods       # Grant admin access to a role
!addwhitelist @TrustedBot user "trusted bot"  # Whitelist a user
!setlog security_log #security  # Set security log channel
!setupverification #verify @Verified button 0  # Setup verification
!antinukethreshold channel_delete 5  # Tune AntiNuke threshold
!antiraidthreshold join_threshold 15  # Tune AntiRaid threshold
!backup  # Create your first backup
```

---

## 📋 Command Reference

### Antinuke
| Command | Description |
|---------|-------------|
| `!antinuke` | View AntiNuke configuration |
| `!antinukethreshold <key> <value>` | Set a threshold |
| `!lockdown` | Toggle server-wide lockdown |

### Antiraid
| Command | Description |
|---------|-------------|
| `!antiraid` | View AntiRaid configuration |
| `!antiraidthreshold <key> <value>` | Set a threshold |
| `!raidmode` | Toggle raid mode |
| `!quarantine <member> [reason]` | Quarantine a member |
| `!unquarantine <member>` | Release from quarantine |
| `!reportdmraid` | Report a DM raid |

### Automod
| Command | Description |
|---------|-------------|
| `!automod` | View AutoMod configuration |
| `!automodtoggle <filter>` | Toggle a filter |
| `!badword <word>` | Add custom bad word |
| `!removebadword <word>` | Remove custom bad word |

### Antispam
| Command | Description |
|---------|-------------|
| `!antispam` | View AntiSpam configuration |
| `!antispamthreshold <key> <value>` | Set a threshold |
| `!clearspam <member>` | Clear violations |

### Antitamper
| Command | Description |
|---------|-------------|
| `!antitamper` | View AntiTamper configuration |
| `!protectrole <role>` | Protect a role from modification |

### Moderation
| Command | Description |
|---------|-------------|
| `!ban <member> [reason]` | Ban a member |
| `!unban <user_id>` | Unban a user |
| `!kick <member> [reason]` | Kick a member |
| `!mute <member> [duration] [reason]` | Timeout a member |
| `!unmute <member>` | Remove timeout |
| `!warn <member> <reason>` | Warn a member |
| `!warnings [member]` | View warnings |
| `!purge <count>` | Bulk delete messages |
| `!lock [channel]` | Lock a channel |
| `!unlock [channel]` | Unlock a channel |
| `!slowmode <seconds>` | Set slowmode |
| `!userinfo [member]` | View user info |
| `!serverinfo` | View server info |
| `!history <member>` | View user history |
| `!incidents [resolved]` | View incident reports |
| `!resolveincident <id>` | Resolve an incident |

### Verification
| Command | Description |
|---------|-------------|
| `!setupverification <channel> <role> [method] [min_age]` | Setup verification |
| `!verification` | View verification config |
| `!disableverification` | Disable verification |
| `!verify` | Manually verify |

### Backup
| Command | Description |
|---------|-------------|
| `!backup` | Create a backup |
| `!backups` | List backups |
| `!restorebackup <id> [mode]` | Restore a backup |
| `!confirmrestore <id>` | Confirm restore |
| `!deletebackup <id>` | Delete a backup |

### Logging
| Command | Description |
|---------|-------------|
| `!setlog <type> [channel]` | Set a log channel |
| `!logchannels` | View log channels |
| `!auditlog [limit]` | View audit log |

### Analytics
| Command | Description |
|---------|-------------|
| `!securityscore` | View server security score |
| `!serverstats` | View server statistics |
| `!modstats` | View moderation metrics |
| `!topmembers [criterion]` | Top members (joined/oldest/newest) |
| `!scorehistory` | Security score history |

### Scheduled Tasks
| Command | Description |
|---------|-------------|
| `!schedule <action> <schedule>` | Schedule a task |
| `!tasks` | List scheduled tasks |
| `!deletetask <id>` | Delete a task |
| `!toggletask <id>` | Enable/disable a task |

### Settings
| Command | Description |
|---------|-------------|
| `!settings` | View all settings |
| `!setprefix <prefix>` | Change prefix |
| `!setlanguage <lang>` | Change language |
| `!addadminrole <role>` | Add admin role |
| `!removeadminrole <role>` | Remove admin role |
| `!adminroles` | List admin roles |
| `!addwhitelist <entity> [reason]` | Add to whitelist |
| `!removewhitelist <entity>` | Remove from whitelist |
| `!whitelist [type]` | List whitelist |
| `!clearwhitelist` | Clear whitelist |
| `!resetsettings` | Reset to defaults |
| `!botinfo` | Bot information |
| `!ping` | Latency check |
| `!help [category]` | Help menu |

### Slash Commands
The following slash commands are also available:
- `/ban`, `/kick`, `/mute`, `/purge`
- `/ping`, `/botinfo`, `/securityscore`

---

## 🏗️ Architecture

```
securitybot/
├── bot/
│   ├── __init__.py
│   ├── main.py                  # Entry point
│   ├── core/
│   │   ├── config.py            # Env-based configuration
│   │   ├── database.py          # Async SQLite layer
│   │   ├── bot.py               # Bot class & cog loading
│   │   ├── logger.py            # Rotating log setup
│   │   ├── permissions.py       # 5-tier permission system
│   │   ├── whitelist.py         # Whitelist management
│   │   ├── i18n.py              # Multi-language support
│   │   └── security_score.py    # 0-100 scoring algorithm
│   ├── cogs/
│   │   ├── antinuke.py          # Anti-Nuke protection
│   │   ├── automod.py           # Message content filtering
│   │   ├── antiraid.py          # Raid detection & mitigation
│   │   ├── antispam.py          # Spam rate limiting
│   │   ├── antitamper.py        # Config integrity protection
│   │   ├── moderation.py        # Ban/kick/mute/warn etc.
│   │   ├── verification.py      # Verification system
│   │   ├── backup.py            # Backup & restore
│   │   ├── logging_cog.py       # Audit & event logging
│   │   ├── analytics.py         # Stats & security score
│   │   ├── scheduled_tasks.py   # Scheduled actions
│   │   ├── settings.py          # Per-guild configuration
│   │   ├── events.py            # Global error handling
│   │   └── slash.py             # Slash command mirror
│   ├── utils/
│   │   ├── constants.py         # Colors, icons, brand
│   │   ├── embeds.py            # Embed factory
│   │   └── helpers.py           # Time/regex/discord helpers
│   └── locales/
│       ├── en.json
│       ├── es.json
│       ├── fr.json
│       ├── de.json
│       ├── pt.json
│       └── hi.json
├── data/                        # SQLite DB (auto-created)
├── logs/                        # Rotating logs
├── backups/                     # Server backups
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── nixpacks.toml                # Railway config
├── .env.example
└── README.md
```

### Database Schema

The bot uses SQLite with WAL mode. Key tables:

- `guilds`, `guild_settings` — per-guild config
- `whitelist` — whitelisted entities
- `admin_roles` — admin role assignments
- `verifications`, `verification_config` — verification system
- `log_channels` — log channel assignments
- `audit_log` — all logged actions
- `incidents` — incident reports
- `user_history` — per-user moderation history
- `antinuke_events` — AntiNuke event tracking
- `spam_tracker` — spam detection state
- `backups` — backup metadata
- `scheduled_tasks` — scheduled jobs
- `quarantined_users` — quarantine state
- `security_score_history` — score over time
- `raid_events` — raid records

---

## 🔧 Default Thresholds

These defaults are sensible starting points. Adjust per-guild with the `!antinukethreshold`, `!antiraidthreshold`, `!antispamthreshold`, and `!automodtoggle` commands.

### AntiNuke
| Setting | Default | Description |
|---------|---------|-------------|
| `channel_delete` | 3 | Max channels deletable in 10s |
| `channel_create` | 5 | Max channels creatable in 10s |
| `role_delete` | 3 | Max roles deletable in 10s |
| `role_create` | 5 | Max roles creatable in 10s |
| `webhook_create` | 2 | Max webhooks creatable in window |
| `emoji_delete` | 3 | Max emojis deletable in window |
| `sticker_delete` | 2 | Max stickers deletable in window |
| `bot_add` | 1 | Zero-tolerance for unauthorized bots |
| `member_kick` | 3 | Max kicks in window |
| `member_ban` | 3 | Max bans in window |
| `member_timeout` | 5 | Max timeouts in window |
| `vanity_change` | 1 | Any vanity URL change triggers |
| `server_update` | 1 | Any server update triggers |
| `window_seconds` | 10 | Detection window |
| `punishment` | strip | strip/ban/kick/quarantine |

### AntiRaid
| Setting | Default | Description |
|---------|---------|-------------|
| `join_threshold` | 10 | Joins in window |
| `join_window_seconds` | 10 | Detection window |
| `leave_threshold` | 8 | Leaves in window |
| `leave_window_seconds` | 15 | Detection window |
| `dmraid_threshold` | 5 | DM raid reports in window |
| `dmraid_window_seconds` | 60 | DM raid window |
| `new_account_hours` | 24 | Accounts younger than this are suspicious |
| `auto_lockdown_on_raid` | true | Auto-lockdown on raid |
| `punishment` | kick | kick/ban/quarantine |

### AntiSpam
| Setting | Default | Description |
|---------|---------|-------------|
| `message_threshold` | 7 | Messages in window |
| `window_seconds` | 5 | Detection window |
| `mention_threshold` | 5 | Mentions in single message |
| `duplicate_threshold` | 3 | Identical messages in window |
| `caps_percentage` | 70 | % caps to trigger |
| `caps_min_length` | 8 | Min length for caps check |
| `emoji_max_per_message` | 15 | Max emojis per message |
| `attachment_threshold` | 5 | Attachments in window |
| `punishment` | timeout | timeout/kick/ban |
| `timeout_seconds` | 600 | Timeout duration |

---

## 🛡️ Permission System

The bot uses a 5-tier permission model (highest priority wins):

1. **Bot Owner** (env `OWNER_ID`) — full access, bypasses ALL security checks
2. **Guild Owner** — full access to their guild, bypasses ALL security checks
3. **Whitelisted Users** — bypass all security checks (AntiNuke, AutoMod, etc.)
4. **Admin Roles** (set via `!addadminrole`) — can use all admin commands
5. **Discord Administrator permission** — can use admin commands

All commands require either Administrator Discord permission OR an admin role assignment from the owner/admin.

---

## 📊 Security Score

The `!securityscore` command calculates a 0-100 score based on:

- Verification enabled (+10 / -10)
- 2FA required for mods (+5 / -5)
- Log channels configured (+5 / -5)
- Whitelist size (sensible +5 / too broad -5)
- Admin count (tight +5 / too many -10)
- Bot count (low +5 / high -5)
- Verification level (high/highest +5 / low -5)
- Recent incidents (clean +5 / critical -10)

Score history is tracked over time.

---

## 🔔 Owner Notifications

The configured owner (`OWNER_ID`) receives DM notifications for:
- ✅ Bot startup (with server count)
- 🚨 Anti-Nuke triggers
- 🚨 Anti-Raid triggers
- 🚨 AntiTamper violations (token sharing, etc.)
- ➕ Added to new servers
- ➖ Removed from servers
- 🔧 Manual raid mode toggle
- 🔒 User quarantined

This ensures the owner is **always** aware of critical events in real-time.

---

## 🧪 Testing

Set `DRY_RUN=true` in `.env` to disable destructive actions during testing.

---

## 📦 Production Deployment

### Railway
1. Push to GitHub
2. Create Railway project from repo
3. Add `DISCORD_TOKEN` and `OWNER_ID` env vars
4. Attach a volume at `/app/data` for SQLite persistence
5. Deploy

### Docker
```bash
docker compose up -d
docker compose logs -f
```

### VPS / Bare Metal
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Use systemd, supervisor, or pm2 to run `python -m bot.main` as a service
```

---

## 🤝 Comparison to SecurityBot / Wick / AuthGG

| Feature | SecurityBot Enterprise | SecurityBot | Wick | AuthGG |
|---------|------------------------|-------------|------|--------|
| Anti-Nuke | ✅ Full | ✅ | ✅ | ✅ |
| AutoMod | ✅ Full | ✅ | ✅ | ❌ |
| AntiRaid | ✅ Full | ✅ | ✅ | ❌ |
| AntiSpam | ✅ Full | ✅ | ✅ | ❌ |
| AntiTamper | ✅ Full | ✅ | ✅ | ❌ |
| Verification | ✅ | ✅ | ✅ | ✅ |
| Backup | ✅ | ✅ | ❌ | ❌ |
| Audit logs | ✅ | ✅ | ✅ | ❌ |
| Incident reports | ✅ | ❌ | ✅ | ❌ |
| Security score | ✅ | ❌ | ❌ | ❌ |
| Owner DM alerts | ✅ | ✅ | ✅ | ❌ |
| Multi-language | ✅ 6 langs | ✅ | ✅ | ❌ |
| Slash commands | ✅ | ✅ | ✅ | ❌ |
| Self-hostable | ✅ | ❌ | ❌ | ❌ |
| Open source | ✅ | ❌ | ❌ | ❌ |

---

## 📝 License

MIT License. See [LICENSE](LICENSE).

---

## ⚠️ Disclaimer

This bot is provided as-is. Always test in a development server before deploying to production. The owner is not responsible for any damages caused by misuse or misconfiguration of this software.

---

**Built with ❤️ for the Discord community.**
