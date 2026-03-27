# HM Coaching Agent — Design Spec

## Overview

A personal AI coaching agent that helps Rafee achieve a 1:35 half marathon goal (race day: May 31, 2026). The agent pulls fitness data from Garmin Connect and Strava, compares it against a training plan in Google Sheets, and provides daily coaching feedback via a two-way Telegram bot.

## Goals

- Automate daily training plan delivery and post-activity analysis
- Detect missed workouts and offer rescheduling
- Provide personalized feedback based on actual performance vs plan
- Adapt the training plan weekly based on compliance and fitness trends
- Enable two-way conversation for ad-hoc coaching questions

## Architecture

Single Python service running on macOS via `launchd`.

```
┌──────────────────────────────────────────────────┐
│  Python Service (launchd)                        │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ Garmin   │  │ Strava   │  │ Google Sheets │  │
│  │ (garth)  │  │ (stravalib)│ │ (gws CLI)     │  │
│  └────┬─────┘  └────┬─────┘  └──────┬────────┘  │
│       └──────┬───────┘───────────────┘           │
│         ┌────▼─────┐                             │
│         │  Claude  │  (Agent SDK + Max sub)      │
│         │ Analyzer │                             │
│         └────┬─────┘                             │
│         ┌────▼─────┐                             │
│         │ Telegram │  (two-way chat)             │
│         │   Bot    │                             │
│         └──────────┘                             │
└──────────────────────────────────────────────────┘
```

## Data Sources

### Garmin Connect (via garth library)
- HRV, resting HR, stress level
- Sleep score, sleep stages, Body Battery
- Training status, training load, VO2 max
- Recovery time, intensity minutes
- Daily steps, active calories

### Strava (via stravalib + webhook)
- Activity data: distance, pace, splits, HR zones, cadence, elevation, GAP, relative effort
- Webhook push notification on new activity upload

### Google Sheets (via gws CLI)
- **Original plan sheet** (read-only): "1:35 Hour HM Training Plan"
  - Spreadsheet ID: `1wwH1zlAWDVWLffrSa9CFum0AWYJeVOuvIJr-U1xmzOU`
  - Tabs: "KM" (main plan), "Guidance Notes"
- **Active plan sheet** (cloned, writable): working copy updated weekly by the agent based on performance
- 21-week plan: Base (wk 1-7), Build (wk 8-13), Peak (wk 14-18), Taper (wk 19-21)
- Weekly pattern: Mon=Swim, Tue=Intervals, Wed=Easy, Thu=Threshold, Fri=Easy, Sat=Rest, Sun=Long Run
- Key paces: Easy Z2 ~6:30/km, Threshold ~4:38-4:49/km, Intervals ~4:05-4:28/km

## Triggers & Notifications

| Trigger | Time | Action |
|---|---|---|
| Morning brief | 5am | Pull Garmin health data (HRV, sleep, Body Battery). Read today's plan from active sheet. Claude analyzes readiness and delivers plan + health summary via Telegram. |
| Post-activity | Strava webhook | Compare actual vs planned workout. Claude generates performance feedback. Send via Telegram. |
| Missed workout | 11pm | If no activity logged, notify via Telegram and offer to reschedule. |
| Weekly summary | Sunday evening | Summarize week: compliance %, highlights, concerns. Update next week in active plan sheet. |
| Ad-hoc chat | Anytime | User messages Telegram bot. Claude responds with context-aware coaching advice. |

## Telegram Bot Interactions

### Morning brief (5am)
```
Today's plan: Easy 10km @ 6:30/km
Sleep: 7.2h (score 81) | HRV: 54 (above avg) | Body Battery: 72
You're in good shape. Enjoy the easy run.
```

### Post-activity feedback
```
Easy run done! 10.3km @ 6:22/km, avg HR 142 (Z2)
Right on plan. Weekly progress: 38/55km (69%)
Tomorrow: Threshold 8km @ 4:45/km
```

### Missed workout (11pm)
```
Hey, no activity logged today. Planned: Intervals 6x1km.
That's OK — want to shift it to tomorrow, or skip it this week?
```

### Ad-hoc conversation
```
You: "my knee feels sore after yesterday's long run"
Bot: "Your long run pace was 15s/km faster than planned and you logged
22km vs 18km planned. The extra load + pace likely caused it. Suggest:
skip tomorrow's intervals, do easy 5km or rest. If soreness persists
48h, consider seeing a physio."
```

### Weekly summary (Sunday evening)
```
Week 10 summary:
Planned: 55km | Actual: 48km (87%)
Missed: Thursday threshold (swapped to rest)
Highlights: Tuesday intervals were strong — 4:12/km avg
Adjusting next week's plan in your active sheet...
```

## Claude Analysis Context

Each time Claude is called, it receives:

- Today's planned workout (from active sheet)
- Actual activity data (from Strava, if available)
- Garmin health metrics (HRV, sleep, Body Battery, training load, VO2 max, recovery time)
- Activity history and trends (from local SQLite)
- Plan compliance stats (weekly/overall)
- Race date and time remaining (May 31, 2026)
- Training phase context (Base/Build/Peak/Taper)

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12+ |
| Telegram bot | python-telegram-bot |
| Claude integration | anthropic Python SDK (via Max subscription API access) |
| Garmin data | garth |
| Strava data | stravalib (polling every 15min for MVP, webhooks for v2) |
| Google Sheets | gws CLI (subprocess) |
| Database | SQLite3 (built-in) |
| Process manager | macOS launchd |
| Scheduling | python-telegram-bot job_queue (for 5am/11pm/Strava polling) |

## Project Structure

```
running/
├── src/
│   ├── bot.py              # Telegram bot (main entry point, always running)
│   ├── coach.py            # Anthropic SDK — analysis & feedback logic
│   ├── garmin.py           # garth — pull HRV, sleep, Body Battery, etc.
│   ├── strava.py           # Strava OAuth + webhook handler
│   ├── planner.py          # Google Sheets read/clone/update via gws CLI
│   └── db.py               # SQLite — activity history, plan state
├── config/
│   └── settings.yaml       # API keys, sheet IDs, timing, preferences
├── data/
│   └── coach.db            # SQLite database
├── launchd/
│   └── com.rafee.runcoach.plist  # macOS service definition
├── requirements.txt
└── README.md
```

## Setup Requirements

1. **Telegram bot token** — create via @BotFather (~2 min)
2. **Strava API app** — register at developers.strava.com (~5 min)
3. **Garmin credentials** — existing Garmin Connect login (used by garth)
4. **gws CLI** — already installed and authenticated (mdrafee03@gmail.com)
5. **Claude Max subscription** — already active (for Agent SDK)

## Training Plan Management

- **Original sheet**: read-only reference, never modified
- **Active plan sheet**: cloned from original, agent updates weekly
- Weekly adjustments based on: compliance %, fitness trends, fatigue indicators, missed workouts
- Agent explains every plan change in the weekly summary

## Strava Webhook Ingress

Strava webhooks need a public URL to reach the local Mac. **MVP approach: poll Strava every 15 minutes** instead of webhooks. This avoids the complexity of tunneling and is fast enough for coaching feedback.

For v2, options include:
- Cloudflare Tunnel (free, stable) pointing to the local Flask server
- Tailscale Funnel
- A lightweight cloud relay (Cloudflare Worker)

## Claude Integration

Use the `anthropic` Python SDK (not a separate "Agent SDK" package). The Max subscription provides API access. The `coach.py` module constructs prompts with training context and calls `anthropic.messages.create()`.

Expected token usage per call: ~2k input + ~500 output tokens. Daily budget: ~5 calls (morning, post-activity, missed check, ad-hoc) = well within Max limits.

## Security

- **Telegram bot restricted by `chat_id`** — only Rafee's Telegram account can interact
- **Secrets stored in `.env` file** (not committed to git): Telegram token, Strava client secret, Garmin credentials, Anthropic API key
- `settings.yaml` contains only non-sensitive config (sheet IDs, timing, preferences)

## Error Handling

| Failure | Behavior |
|---|---|
| garth fails (Garmin down) | Send morning brief without health data, note "Garmin data unavailable" |
| Strava poll returns no new activity | Silently retry next cycle |
| Strava OAuth token expired | Auto-refresh using refresh token (stravalib handles this) |
| Claude API call fails | Retry once after 30s. If still fails, send raw data without analysis |
| Mac was asleep at 5am | launchd fires the job when Mac wakes, with a note about the delay |
| gws CLI fails (Sheets) | Use cached plan data from SQLite |

## Data Model (SQLite)

| Table | Purpose | Key Columns |
|---|---|---|
| `activities` | Strava activity history | id, date, type, distance, pace, hr_avg, hr_zones, splits, planned_workout |
| `health_metrics` | Daily Garmin data | date, hrv, resting_hr, sleep_score, body_battery, vo2max, training_load, recovery_time |
| `plan_days` | Cached training plan | date, week_num, phase, workout_type, target_distance, target_pace, actual_status |
| `plan_changes` | Audit log of plan modifications | date, week_num, field_changed, old_value, new_value, reason |
| `conversations` | Telegram chat context | timestamp, role, message (last 20 messages for context) |

## Training Plan Management

- **Original sheet**: read-only reference, never modified
- **Active plan sheet**: cloned on first run via `gws sheets` copy. Sheet ID stored in `settings.yaml`
- **Weekly updates**: agent modifies target cells (distance, pace) in the active sheet every Sunday. Changes logged in `plan_changes` table for audit/rollback
- Weekly adjustments based on: compliance %, fitness trends, fatigue indicators, missed workouts
- Agent explains every plan change in the weekly summary

## Mid-Plan Bootstrap

The agent joins at ~week 12 of 21. On first run:
- Read full plan from Google Sheet to establish all 21 weeks
- Mark weeks 1-12 as "pre-agent" (no compliance tracking)
- Pull recent Garmin/Strava history (garth supports backfill) to establish baseline metrics
- Begin tracking from current week forward

## Scope

- **In scope**: all running workouts (easy, intervals, threshold, long run), cross-training swim sessions (Monday), gym sessions
- **Out of scope**: nutrition, non-plan activities, race-day logistics

## Constraints

- Mac must be running for the service to work
- Garmin data via garth depends on unofficial API (may break with Garmin updates)
- Claude API token usage counts against Max subscription limits

## Logging

- Structured logging via Python `logging` module
- Log file: `data/coach.log`
- Log rotation: 7 days, max 10MB per file
- Levels: INFO for normal operations, WARNING for degraded (e.g., Garmin unavailable), ERROR for failures

## Process Architecture

- `bot.py` is the single entry point, runs the Telegram bot event loop
- Strava polling runs as a background task within the same async event loop (no separate Flask server needed for MVP)
- 5am/11pm triggers via `python-telegram-bot` `job_queue` (reliable within the long-running process)
- `launchd` manages process lifecycle: auto-start on boot, restart on crash

## Race Timeline

- Race: May 31, 2026
- Current date: March 27, 2026
- Weeks remaining: ~9
- Training phase: likely Build/Peak transition
