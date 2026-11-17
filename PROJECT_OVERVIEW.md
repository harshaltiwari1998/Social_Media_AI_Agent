# Project overview - HarryBeast content pipeline

> A map for a new session: read this first, then `CLAUDE.md` for the full rules and history.
> Snapshot written 2026-11-17. Live numbers (queues, what is posted) change daily - check them, don't trust old counts.

## 1. What this is, in one paragraph

A Python pipeline that writes fact-checked scripts, turns them into videos (pictures + edge-tts narration + captions,
AI-animated Pokemon clips, or cuts of the owner's own game streams) and uploads them to YouTube, Facebook and
Instagram. Gaming content is English, movie content is Hindi. It runs partly on GitHub Actions (public repo) and partly
on the owner's Windows laptop. The repo started as a FastAPI "universal agent" scaffold (`backend/app/main.py`,
`app/api`, `app/agent`, `app/tools`) - that part is unused; the real work is in `backend/scripts/` and
`backend/app/{media,social,pipeline,trends,analysis}`.

## 2. Status right now (read before doing anything)

- **Nothing is posted without the owner's approval.** On 2026-11-16 the YouTube channel got a community-guideline
  strike for an adult game in a deal short (it had earlier been terminated over another adult game and got it back on
  appeal). Since then:
  - every GitHub workflow is **manual only** (`workflow_dispatch`); only `token-check.yml` and `weekly-report.yml`
    still run on a schedule, and they post nothing;
  - laptop batches run with `POST_MODE=review`: finished videos go to `<run folder>/review_queue.json`, the owner
    watches them (`post_reviewed.py <run> show`), then `approve 1,3` / `reject 2`;
  - every video goes through the NudeNet check (`app/media/safety.py`) before review or posting.
- **YouTube uploads are frozen** for about a week from ~2026-11-16 (the strike). Instagram and the gaming
  Facebook Page still take approved videos.
- Laptop scheduled tasks (Windows Task Scheduler):

  | Task | State | What it does |
  |---|---|---|
  | Pokemon Evolution Daily (20:15 IST) | on | builds up to 3 evolution shorts, posts nothing, opens them in VLC for review |
  | Car Museum Daily (18:00) | on | posts 3 of the 25 owner-approved Payana Museum shorts (Instagram + gaming FB) |
  | Yu-Gi-Oh Daily (19:00) | on | posts the next 3 approved Yu-Gi-Oh explainers to Instagram |
  | PC Health Check (15 days) | on | read-only laptop check (`F:/AI AGENT/pc_cleanup/`) - not part of the pipeline |
  | AI Trend Poster Daily | **disabled** | old trend pipeline + daily_job on the laptop |

- Open items:
  - `backend/scripts/fal_video.py` has an **uncommitted** fix: narration blocks no longer overlap at the end of
    evolution shorts (owner: "aakhri me 2 aavaj aati he"); the last clip is held longer instead.
  - `backend/scripts/drive_login.py` is an old untracked script (Google Drive route was declined) - unused.
  - Yu-Gi-Oh YouTube copies wait for the end of the strike week (`.cache/ygo_post.py N youtube`).
  - GTA 6 launches 2026-11-19; the owner streams it - clip that stream later like the RDR2 streams.
  - Compare gaming Facebook Page views again in early December (volume was cut on 2026-11-14).
  - Yu-Gi-Oh ideas the owner liked: Duel Links / Master Duel "best deck" videos, Duel Links card explainers, a deck
    plan from the owner's own collection.

## 3. Channels

| Platform | Account | Gets |
|---|---|---|
| YouTube | **HarryBeast Gaming** | gaming shorts + long videos (English) |
| YouTube | **Name Writer / CineAI Hindi** | movie shorts + long movie videos (Hindi) |
| Facebook Page | Gaming Page | ONLY Pokemon evolutions, fusions, Mewtwo stones, max 2 own-stream clips a day, museum shorts |
| Instagram | @harrybeastgaming | the gaming shorts |
| Facebook Page | "AI Trend Poster" (main) + its Instagram | Hindi movie shorts, reels, comedy reels, 1 combined daily rashifal |

Gaming never goes to the movie accounts and the other way round.

## 4. How a video is made (the flow)

```
spec JSON (hand/routine-written)      live data (store_deals, PokeAPI, YGOPRODeck)      own stream recording
            \                                   |                                         /
             +---------> build: game_videos.py / cinema_video.py / long_video.py / pokemon_daily.py / gameplay_clips.py
                                                |
                         safety.scan_video (NudeNet, fails closed)
                                                |
                  POST_MODE=review -> review_queue.json + "hold:review" marker on each platform
                                                |
                         owner watches -> post_reviewed.py approve N  (or reject N)
                                                |
                  upload: app/social/youtube.py, facebook.py, instagram.py (+ cloudflared tunnel for IG)
                                                |
           record in pipeline_store.sqlite3 -> merge_state.py -> pushed to the `gaming-state` branch
```

`scripts/daily_job.py` is the single entry point for spec content (retries pending uploads, then builds + posts each
slot). It picks the lowest-`order` unposted, unexpired spec per slot; laptop batches use `--from-end`.

## 5. Repo map (`F:/AI AGENT/universal-ai-agent`)

| Path | What |
|---|---|
| `CLAUDE.md` | full project rules, decisions, history - keep it updated with every change |
| `.github/workflows/` | 12 workflows (daily-content, daily-anipal, daily-palguide, daily-long, daily-deals, daily-pokemon, daily-gameplay, daily-rashifal, fusion-inbox, token-check, weekly-report, gameplay-probe) |
| `backend/app/media/` | `video_gen.py` (renderer), `text_render.py` (Hindi shaping), `image_gen.py` (local SD), `safety.py` (NudeNet) |
| `backend/app/social/` | YouTube, Facebook, Instagram uploaders, `public_tunnel.py` (cloudflared) |
| `backend/app/pipeline/store.py` | posting store (sqlite), `fb_gaming_allowed` volume gate |
| `backend/app/trends/`, `analysis/` | old trend pipeline (Reddit, Google Trends, HF, GitHub) + Ollama analysis |
| `backend/scripts/` | all pipeline scripts (see section 6) |
| `backend/scripts/specs/` | content specs (see section 7) |
| `backend/.cache/` | laptop-only helpers + tokens + model caches (gitignored). One-off scripts live here: `post_one.py`, `post_queue.py`, `ygo_post.py`, `carmuseum_*.py`, `if_hd.py`, `if_video.py`, `wan_fetch.py`, `channel_scan.py`, `yugioh_write.py` ... |
| `backend/.env` | secrets (gitignored) - never print or commit |
| `inbox/` | Gemini-made Pokemon fusion clips the owner uploads (`fusion-inbox.yml`) |

## 6. Main scripts (`backend/scripts/`)

| Script | Job |
|---|---|
| `daily_job.py` | entry point: retry pending + build/post every slot (`--extra-slot slot:N`, `--long-videos`, `--deals-*`, `--from-end` ...) |
| `laptop_batch.py <tag> ...` | laptop batch in a fresh `F:/AI AGENT/run_<tag>` folder from the latest `gaming-state`, then merge + push; `--merge-only` after approvals |
| `post_reviewed.py <run> show / approve / reject` | the owner-approval step |
| `merge_state.py` | the ONLY way to write posting state into `gaming-state` |
| `game_videos.py` | shorts from specs (Steam trailer footage, Wan-animated Pals) |
| `cinema_video.py` | full-screen "cinema" style (all movie shorts + Yu-Gi-Oh) |
| `long_video.py` | long videos (9-12 sections) |
| `palworld_breeding_videos.py` | breeding cards (3 or 5 verified combos) |
| `store_deals.py` | live Epic/Steam deals -> generated specs (adult filter `_adult`) |
| `pokemon_daily.py` | Pokemon evolution shorts (official art + Wan 2.2 on HF ZeroGPU) and fusion families; `--review` |
| `evolution_review.py`, `evolution_job.cmd` | the 20:15 laptop evolution task |
| `fal_video.py` | motion helpers: Wan clips, drawn evolve/fuse/punch/action clips, `hd_clip` (RIFE + Real-ESRGAN) |
| `stone_forms.py` | Mewtwo + evolution-stone "What If" shorts |
| `fusion_inbox.py` | inbox clips -> 9:16 shorts |
| `gameplay_clips.py`, `gameplay_daily.py`, `gameplay_cloud.py` | own-stream clipping, laptop rendering, `clips/<plan>` branches for CI posting |
| `rashifal.py` | Hindi daily rashifal (skyfield moon positions), one combined video a day |
| `youtube_playlists.py` | puts pipeline uploads into category playlists (quota-capped: max 8 adds a run) |
| `humor.py` | one light on-topic joke per video |
| `youtube_login.py`, `meta_gaming_login.py`, `ops_report.py` | logins, token check, weekly report |
| `motion_daily.py`, `lucario_*.py`, `run_pipeline.py`, `make_topic_content.py` | older experiments / trend pipeline |

## 7. Content and where its specs live

| Content | Spec / source | Notes |
|---|---|---|
| Gaming shorts | `specs/games/<slot>_NN_<id>.json` - slots `new`, `classic`, `aniimo`, `palguide`, `palbase`, `palchain`, `gamenews`, `gow`, `dml`, `gta6`, `yugioh` | English |
| Movie shorts | `specs/movies/` - `movie_new`, `movie_review`, `reel`, `comedy`, `movie_news`, `tribute` | Hindi, cinema style |
| Long videos | `specs/long/{gaming,movies}_NN_<id>.json` (`"series"` reserves Palworld/Aniimo/DML ones) | >= 1200 words EN / 900 HI |
| Palworld breeding | `specs/palworld_queue.json` (+ `palworld_<pal>.json`) | 3 combos, round 2 = 5 new combos |
| Store deals | generated live into `specs/deals/` (gitignored) | no adult games |
| Pokemon evolutions | `specs/pokemon_queue.json` | reviewed before upload |
| Pokemon / DBZ / Pal fusions | `specs/fusion_pairs.json`, `dbz_fusion_pairs.json`, `pal_fusion_pairs.json` | current look: Infinite Fusion sprite -> "3D + aura" redraw + one Wan clip; Pal fusions rejected - don't make more |
| Mewtwo stones | `specs/mewtwo_stones.json` | all 8 + compilation done |
| Own streams | `specs/gameplay/<plan>.json` (19 plans) | RDR2 all rendered in `F:/AI AGENT/gameplay_out/`; recordings deleted - those clips are the only copy |
| Yu-Gi-Oh | `specs/games/yugioh_NN_*.json` (50) | review queue `F:/AI AGENT/run_ygo/review_queue.json` |
| Payana Museum | owner's phone videos, `.cache/carmuseum_*.py` | approved, 3 a day to IG + gaming FB |
| Rashifal | computed (no spec) | AI Trend Poster FB only, now needs approval too |

Who writes specs: two Claude Code cloud routines - "Daily Aniimo + Palworld writer" and "Weekly content refill"
(Mon/Thu) - commit them to the repo. The posting workflows that would use them are manual now.

## 8. State and markers

- **`gaming-state` branch**: `pipeline_store.sqlite3`, `game_videos.json`, `palworld_videos.json` - every run
  restores it, runs, and merges back with `merge_state.py` (never copy a state file over it).
- **`motion-cache` branch**: Wan clips between CI runs. **`clips/<plan>` branches**: rendered stream clips for CI.
- Per-platform markers in the store: `hold:review` (waiting for the owner), `skip:rejected`, `skip:fb-volume`
  (not for the gaming FB Page), `skip:before-gaming-page` (old catalogue - never remove).
- Laptop run folders `F:/AI AGENT/run_*` hold local review queues; `wt_*` folders are temporary.

## 9. Outside services and credentials (names only)

YouTube Data API (OAuth tokens in `backend/.cache/youtube_token*.json`, ~10,000 units/day shared by both channels,
resets ~12:30-13:30 IST), Meta Graph API (Page tokens in `.env`), Hugging Face PRO (`HF_TOKEN`: ZeroGPU quota resets
~19:57 IST - evolution task goes first; credits pay for fal-ai Wan / Qwen through Inference Providers), edge-tts,
Wikipedia/Commons, Steam, Epic, PokeAPI, YGOPRODeck, cloudflared (`F:/AI AGENT/tools`). GitHub secrets mirror `.env`.
**The repo is public** - never commit a token, cookie or private file; no `pull_request` triggers.

## 10. Owner rules in short (full list in CLAUDE.md)

1. Owner approves every video before it goes out; look at every picture/clip yourself first.
2. Zero adult / sexual content, no gore close-ups, hate symbols, slurs, politics, religion messaging.
3. No third-party film/TV clips; gameplay only from the owner's own streams.
4. Facts need two sources (or the subject's Wikipedia article for evergreen facts).
5. Gaming = English, movies = Hindi, never cross the accounts.
6. Everything installed/cached on **F:** (C: is nearly full).
7. Ask before running tests. Update `CLAUDE.md` (+ change log) with every change.
8. Titles: no `.` `?` `<` `>`.

## 11. Gotchas a new session hits

- GitHub Actions start ~6-7 h late; CI logs need a login (use the public jobs API for step timings).
- Instagram needs a public URL (cloudflared tunnel) and light ~5 Mbps copies of big videos, or processing times out.
- Facebook files over 80 MB use the resumable upload.
- edge-tts sometimes returns "No audio was received" - retried 4x.
- `ollama.exe list` without a running server starts Ollama on C: - run `ollama.exe serve` with `OLLAMA_MODELS` set.
- A Wan clip can change the creature's look - check a frame strip of every AI clip before showing it.
- Owner talks in Hinglish; reply in the same style, keep files/docs in English.
