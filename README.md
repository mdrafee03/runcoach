# RunCoach - AI Half-Marathon Coaching Agent

A personal AI coaching agent that pulls your fitness data from Garmin Connect and Strava, compares it against your training plan in Google Sheets, and provides daily coaching feedback via a two-way Telegram bot.

Powered by Claude (Anthropic) via your Pro/Max subscription or API key.

## What It Does

| Feature | Trigger | What Happens |
|---|---|---|
| Morning brief | 5am daily | Pulls Garmin health data (HRV, sleep, Body Battery) + today's plan. Sends readiness assessment. |
| Post-workout analysis | You message "done" | Pulls latest Strava activity + Garmin health. Gives detailed report: rating/10, pacing analysis, HR zones, volume tracking, recovery advice, next focus. |
| Missed workout check | 11pm daily | If no activity logged, offers to reschedule. |
| Weekly summary + auto-adjust | Sunday 8pm | Week compliance %, highlights, concerns. **Automatically adjusts next week's plan** based on performance, fatigue, and missed workouts. Changes are applied to the database and logged for audit. |
| Two-way coaching chat | Anytime | Ask anything: "should I skip today?", "my knee hurts", "how's my week going?" |

## Architecture

Single Python async service running on your Mac via `launchd`.

```
Garmin Connect (garth) ──┐
                         ├── Claude (analysis) ── Telegram Bot (you)
Strava API (stravalib) ──┤
                         │
Google Sheets (gws CLI) ─┘
```

- **Claude**: Via `claude` CLI (Pro/Max subscription) — $0 extra cost
- **Database**: SQLite for activity history, health metrics, plan state, conversations
- **Scheduling**: APScheduler inside the Telegram bot process
- **Process manager**: macOS `launchd` (auto-start on boot, restart on crash)

## Prerequisites

- **macOS** (uses `launchd` for background service)
- **Python 3.13+** (via [uv](https://docs.astral.sh/uv/))
- **Claude Pro ($20/mo) or Max ($100/mo)** subscription with Claude Code installed, OR an Anthropic API key
- **Garmin watch** syncing to Garmin Connect
- **Strava account** (free tier works)
- **Telegram** account (free)
- **Google account** for Sheets access

## Training Plan Template

Clone this Google Sheet as your starting point:

**[1:35 HM Training Plan Template](https://docs.google.com/spreadsheets/d/1wwH1zlAWDVWLffrSa9CFum0AWYJeVOuvIJr-U1xmzOU/edit?usp=sharing)**

This is a 21-week half marathon plan targeting 1:35 (4:30/km pace). You can modify paces, distances, and structure to match your goal. The sheet structure must follow this format:

- Row 1: Title
- Row 2: Column headers (WEEK, phase, Date, Monday, Tuesday, ..., Sunday, Weekly Mileage)
- Each week = 2-3 rows:
  - Row 1: Week number, phase, date, workout types per day
  - Row 2: Distances per day (e.g., "10k", "8k")
  - Row 3 (optional): Paces/details (e.g., "4:45/km")

## Setup Guide

> **AI-friendly**: If you're using Claude Code, Cursor, or any AI assistant to help with setup, give it these instructions and it can walk you through each step.

### Step 1: Clone and install

```bash
git clone https://github.com/mdrafee03/runcoach.git
cd runcoach
uv sync
```

### Step 2: Install gws CLI (Google Workspace CLI)

This is needed to read your training plan from Google Sheets.

```bash
brew install gws
```

Then authenticate:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or use existing)
3. Enable the Google Sheets API and Google Drive API
4. Create OAuth 2.0 credentials (Desktop app type)
5. Download the JSON credentials file
6. Import and authorize:

```bash
gws auth credentials import ~/Downloads/client_secret_XXXXX.json
gws auth add your@gmail.com
```

### Step 3: Create a Telegram bot

1. Open Telegram, message **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g., "My RunCoach")
4. Choose a username (e.g., `my_runcoach_bot`)
5. Save the **bot token** BotFather gives you
6. Message your new bot (send "hello")
7. Get your **chat_id**:

```bash
curl -s "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates" | python3 -m json.tool
```

Look for `"chat": {"id": 123456789}` in the response.

### Step 4: Set up Strava API

1. Go to [developers.strava.com](https://developers.strava.com/)
2. Create an application (any name, "localhost" for callback URL)
3. Note your **Client ID** and **Client Secret**
4. Get a refresh token via OAuth:

```bash
# Open this URL in your browser (replace CLIENT_ID):
# https://www.strava.com/oauth/authorize?client_id=CLIENT_ID&response_type=code&redirect_uri=http://localhost&scope=read,activity:read_all

# After authorizing, you'll be redirected to localhost with a ?code=XXXXX parameter
# Exchange the code for tokens:
curl -X POST https://www.strava.com/oauth/token \
  -d client_id=YOUR_CLIENT_ID \
  -d client_secret=YOUR_CLIENT_SECRET \
  -d code=CODE_FROM_REDIRECT \
  -d grant_type=authorization_code
```

Save the `refresh_token` from the response.

### Step 5: Prepare your training plan

1. Open the [training plan template](https://docs.google.com/spreadsheets/d/1wwH1zlAWDVWLffrSa9CFum0AWYJeVOuvIJr-U1xmzOU/edit?usp=sharing)
2. Click **File > Make a copy**
3. Modify it for your goal pace and schedule
4. Note the **Spreadsheet ID** from the URL: `docs.google.com/spreadsheets/d/<THIS_PART>/edit`

### Step 6: Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_CHAT_ID=your_chat_id_number
GARMIN_EMAIL=your@garmin.email
GARMIN_PASSWORD=your_garmin_password
STRAVA_CLIENT_ID=your_strava_client_id
STRAVA_CLIENT_SECRET=your_strava_client_secret
STRAVA_REFRESH_TOKEN=your_strava_refresh_token
```

### Step 7: Configure settings

Edit `config/settings.yaml`:

```yaml
plan:
  original_sheet_id: "YOUR_GOOGLE_SHEET_ID"
  active_sheet_id: ""  # leave empty, filled on first run
  sheet_range: "KM!A1:Z80"
  start_date: "2026-01-05"  # Monday of week 1 of your plan

schedule:
  morning_brief_hour: 5    # adjust to your wake time
  morning_brief_minute: 0
  missed_check_hour: 23    # when to flag missed workouts
  missed_check_minute: 0
  weekly_summary_day: 6    # 0=Mon, 6=Sun
  weekly_summary_hour: 20

race:
  date: "2026-05-31"       # your race date
  goal_time: "1:35:00"     # your goal time
  distance_km: 21.1
```

### Step 8: Test run

```bash
uv run python -m src.bot
```

On first run, it will:
1. Clone your Google Sheet (creates an "active" working copy)
2. Import all 21 weeks into SQLite
3. Start the Telegram bot

Send `/start` to your bot in Telegram. You should get a welcome message.

### Step 9: Install as background service (macOS)

Edit `launchd/com.rafee.runcoach.plist` — update paths to match your username and install location.

```bash
cp launchd/com.rafee.runcoach.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.rafee.runcoach.plist
```

Verify it's running:

```bash
launchctl list | grep runcoach
```

## Usage

### After a workout
Message your bot: **"done"**, **"finished"**, **"just ran"**, or **"workout done"**

The bot pulls your latest Strava activity + Garmin health data and gives a detailed analysis with:
- Overall rating out of 10
- Pacing analysis (actual vs target, consistency, split analysis)
- Heart rate analysis (zones, efficiency, cardiac drift)
- Volume tracking (weekly progress)
- Recovery assessment (HRV, sleep, Body Battery)
- What went well / what to improve
- Next focus areas

### Ask anything
- "should I skip today's run?"
- "my knee feels sore"
- "how's my training this week?"
- "am I on track for 1:35?"

### Automatic messages
- **5am**: Morning brief with today's plan + readiness check
- **11pm**: Missed workout notification (if applicable)
- **Sunday 8pm**: Weekly summary + adaptive plan adjustments

### Adaptive Plan Adjustments

Every Sunday, the bot reviews your week and automatically adjusts next week's training plan. It considers:
- **Compliance**: If you missed sessions, it may redistribute the load
- **Performance**: If you're ahead of pace targets, it may sharpen intensity
- **Fatigue**: If HRV is trending down or sleep is poor, it reduces volume
- **Race timeline**: Adjustments get more conservative closer to race day

Example output:
```
Week 12 Summary:
Compliance: 71% (5/7 completed, 2 missed)
Volume: 42/55km (76%)
...

Next Week Adjustments:
- Tue: Intervals 10km -> 8km (reduce load after low compliance week)
- Thu: Threshold 4:45/km -> 4:50/km (ease pace, HRV trending down)

✅ 2 plan adjustment(s) applied to next week.
```

All changes are logged in the `plan_changes` database table for full audit history.

## Project Structure

```
runcoach/
├── src/
│   ├── bot.py           # Telegram bot — entry point, scheduling, handlers
│   ├── coach.py         # Claude CLI — prompt builders, analysis
│   ├── garmin.py        # Garmin Connect via garth
│   ├── strava.py        # Strava API via stravalib
│   ├── planner.py       # Google Sheets via gws CLI
│   ├── bootstrap.py     # First-run plan import
│   ├── db.py            # SQLite database
│   └── utils.py         # Shared helpers
├── config/
│   └── settings.yaml    # Non-sensitive configuration
├── launchd/
│   └── com.rafee.runcoach.plist
├── data/                # SQLite DB + logs (gitignored)
├── .env.example
├── pyproject.toml
└── README.md
```

## Troubleshooting

**Bot not responding**: Check `data/runcoach.stderr.log` for errors.

**"Claude not found"**: Make sure Claude Code is installed and the path in `launchd/com.rafee.runcoach.plist` PATH env includes your Claude CLI location.

**"Conflict: terminated by other getUpdates request"**: Two bot instances running. Kill all and restart:
```bash
pkill -f "src.bot"
launchctl unload ~/Library/LaunchAgents/com.rafee.runcoach.plist
launchctl load ~/Library/LaunchAgents/com.rafee.runcoach.plist
```

**Garmin login fails**: Delete `~/.garth` and restart — it will re-authenticate.

**Strava token expired**: Get a new refresh token via the OAuth flow in Step 4.

## License

MIT — use it, modify it, share it.
