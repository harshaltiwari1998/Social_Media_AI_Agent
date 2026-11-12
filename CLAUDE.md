# CLAUDE.md - HarryBeast content pipeline

> **Keep this file current.** After every feature, workflow, schedule or rule change, update the
> matching section below (and "Change log" at the end) in the same commit.

## What this project is

An automated short- and long-video pipeline for the owner's social channels. It writes fact-checked
scripts, renders videos (images + TTS narration + captions, or cuts of the owner's own gameplay
streams) and uploads them on a schedule, mostly without the laptop being on.

| Channel | Content | Language |
|---|---|---|
| **HarryBeast Gaming** - YouTube | Gaming shorts + long videos | English |
| **Gaming Facebook Page** ("Harshal Tiwari") + **Instagram @harrybeastgaming** | Same gaming shorts (long videos: Facebook only) | English |
| **Name Writer / CineAI Hindi** - YouTube | Movie shorts + long movie videos | Hindi |
| **Main Facebook Page** ("AI Trend Poster") + its Instagram | Movie shorts, Hindi movie reels, Hindi comedy reels | Hindi |

## Stack

- **Python 3.12**, code in `backend/` (venv `backend/.venv` on F:). Original scaffold is a FastAPI
  "universal agent" (`backend/app/main.py`, `app/api`, `app/agent`, `app/tools`) - the video pipeline
  lives in `backend/scripts/` and `backend/app/{media,social,pipeline,trends,analysis}`.
- **Video**: moviepy 1.0.3 + ffmpeg (imageio-ffmpeg), Pillow, numpy; Hindi text shaped with uharfbuzz
  (`app/media/text_render.py`). Renderer: `app/media/video_gen.py`.
- **Voice**: edge-tts 7.3.1 (`en-US-GuyNeural` gaming, `hi-IN-SwaraNeural` movies). 7.2.8 got 403s on CI.
  edge-tts now and then answers "No audio was received" - `video_gen.generate_video` retries narration 4x.
- **Images**: Wikipedia/Commons (`wiki:`, `wikifile:` - asked for at 1920 px; a download that times out is retried with the 960 px
  copy: one original that never finished downloading failed every movie long-video run 28 Oct - 11 Nov 2026), Steam screenshots (`steam:<appid>:<n>`), Palworld
  wiki art (`pal:<Name>`), official YouTube frames (`yt:`), official Pokemon artwork on a
  background photo for AI-motion storyboards (`{"poke": name, "background": ref}` in `fal_video.py`, PokeAPI). Local Stable Diffusion 1.5 + x4 upscaler
  (`app/media/image_gen.py`, laptop only). Pollinations balance is empty; Gemini image/Veo need billing.
- **LLM on laptop**: Ollama `gemma3:4b` (trend pipeline). Models live in `backend/.cache/ollama-models`.
- **Upload APIs**: YouTube Data API v3 (`app/social/youtube.py`), Meta Graph API for Facebook/Instagram
  (`app/social/facebook.py`, `instagram.py`; Instagram needs a public URL -> temporary cloudflared tunnel,
  `app/social/public_tunnel.py`). Facebook videos go to `graph-video.facebook.com`; files over 80 MB use the
  resumable upload (start / transfer chunks / finish) - a single request got HTTP 413, so no long video reached the
  gaming Page until 2026-11-10.
- **Scheduling**: GitHub Actions (`.github/workflows/`), Claude Code cloud routines (content writers),
  Windows scheduled task on the laptop (`backend/scripts/daily_job.cmd`).
- **State**: posting history lives on the **`gaming-state`** branch (`pipeline_store.sqlite3`,
  `game_videos.json`, `palworld_videos.json`). Every workflow restores it, runs, and pushes it back.

## How it works

### Specs (what to make)
- `backend/scripts/specs/games/<slot>_NN_<id>.json` - gaming shorts. Slots: `new`, `classic`, `aniimo`,
  `palguide`, `palbase`, `palchain`, `gamenews`, `gow`, `dml` (Dragon Mania Legends breeding, made on request -
  not in any daily workflow; post with `--extra-slot dml:N`).
- `backend/scripts/specs/movies/<slot>_NN_<id>.json` - movie shorts: `movie_new`, `movie_review`,
  `reel` (Facebook + Instagram only via `"platforms"`), `comedy`, `movie_news`, `tribute`.
- `backend/scripts/specs/long/{gaming,movies}_NN_<id>.json` - long videos (9-12 sections, >= 1200 words
  English / >= 900 Hindi). Optional `"series": "palworld" | "aniimo" | "dml"` reserves a spec for the
  Aniimo + Palworld workflow (`--long-series`); normal long runs skip series specs.
- `backend/scripts/specs/palworld_queue.json` - Palworld breeding shorts. Each entry has 3 verified
  `"combos"` for the same child; round 2 entries (`"key": "<Child> 5"`) bring a posted child back with 5 NEW combos
  (picked from PalCalc's 1.0 game-file data, `tylercamp/palcalc` `breeding.json`, and re-checked with the formula).
- **Store deals have no hand-written specs**: `scripts/store_deals.py` checks Epic (free now / next week) and
  Steam India (free-to-keep, Daily/Weekend/Midweek Deal, sale events, franchise sales, top discounted games)
  live each run and generates one-game shorts (id = store + game + offer, so each offer posts once) and up to
  2 long roundups (free + top deals; under Rs 500, else biggest discounts) into `specs/deals/` (gitignored);
  the manifest keeps a `"data"` copy so retries work on CI. Images use `url:<https url>` refs.
- `backend/scripts/specs/pokemon_queue.json` - Pokemon evolution lines (checked against PokeAPI evolution chains)
  for `pokemon_daily.py`; storyboards are generated from them. Style 2 (owner request 2026-11-04): a Wan clip per
  form joined by a locally drawn game-style evolution (`fal_video.evolve_clip`: glow, swapping white silhouettes,
  white screen; next clip fades in via `white_in`), then the line's Mega Evolutions (PokeAPI species varieties with
  `-mega`, or `"megas"` in the entry) with a rainbow aura. Lines cached in the old 4-clip style finish in that style.
  `charmander-mega` (Charmander -> Mega Charizard X and Y) was queued first on request.
- `inbox/<pokemon1>+<pokemon2>[=<name>].mp4` - Pokemon fusion reveal clips the owner makes in the Gemini app
  (prompts: `F:/AI AGENT/pokemon_fusion_prompts.md`) and uploads on the GitHub website; names from
  `backend/scripts/specs/fusion_pairs.json`. The same pairs (with a `"look"` per pair) are also made automatically by
  `pokemon_daily.py`. Fused art (owner 2026-11-08, after a bad Lucarace: must mix BOTH parents part by part like the
  owner's reference - body/mask/appendages of one, ears/fur collar/flame legs of the other): pairs with a `"fuse"`
  text (part-by-part list, both names) go to the `linoyts/qwen-image-2-1-realtime` Space (Qwen-Image-2.1, 2 steps,
  ~13 s / ~20 s GPU): both parents' official art as reference images, prompt asks for ONE new standing creature
  with clear legs - owner approved the result on 2026-11-09 ("mast bane he"; matches the reference Lucarace).
  The prompt demands ONE face with exactly two eyes (owner 2026-11-09: fusions showed both parents' eyes, e.g. a
  Metagross X-face with eyes on Scizor's chest) - check every fused PNG for a second face/eye pair before showing it.
  **Every fusion is shown to the owner before upload, batches included** - the owner names which ones to post.
  Its plain white background is cut by `_key_flat` (border flood fill; enclosed gaps only when within `gap_tol`
  of the background - a looser pass punched a hole in white faces; PIL `fromarray` images must be `.copy()`-ed or
  floodfill does nothing). Tried and dropped: Qwen-2509 "Super-Fusion" LoRA (pasted both parents in their own
  poses - unreadable), official Qwen-2509 Space (TypeError / 10-min queues), FLUX.2-klein + Qwen-2511-Fast
  (RuntimeError). HF credits ($1 / 10 min) only cover over-quota ZeroGPU once the token has the "Make calls to
  Inference Providers" permission (added to the `Laptop` token 2026-11-09). **No Kontext fallback for `fuse` pairs** - a Kontext Darkceus (spider-like) got posted on
  2026-11-09. Pairs without `fuse` (older ones): FLUX.1 Kontext ZeroGPU Space edits the parent whose body the `look` keeps, on a chroma-key green background (magenta for green Pokemon; a white
  one was flood-filled and punched holes in white faces) (the other parent is
  NOT named in the prompt - naming it made Kontext just redraw that Pokemon), and the fusion itself is drawn locally
  (`fal_video.fuse_clip`: both parents' white silhouettes slide together, swap with the fused form, white screen).
  Style 8 since 2026-11-09: parent A still, parent B still, the drawn fusion, the fused creature holds still 3 s,
  then does TWO moves that fit it (owner: "not the same jump for everyone"): `fal_video.action_clip` modes leap,
  blast, powerup, beam, breath, lightning, swirl, teleport, fly, levitate - picked by `pokemon_daily.MOVES` from
  the first/second type; Dragon Ball pairs list their own `moves`. All drawn locally from the fused PNG. **No Wan on fusions**: it turned Darkceus into a different
  bearded character and broke its eyes (owner: "bakwas") - complex fused designs do not survive it. **Automatic fusions are paused** (`--max-fusions` default 0) until the owner approves
  the Qwen art; one-off fusions on request still work. Older-style clips in a pair's cache are thrown away
  (style-2/3 fused art is kept; shared `fusion-<a>+<b>` record).
- Pokemon fusion art since 2026-11-09 (owner: "ek ke upar ek mat karo, bilkul fused lagna chahiye"): text-only
  `fal-ai/qwen-image` through HF credits - NO parent pictures (with them the edit models pasted one Pokemon's parts on
  the other; Sceptile + Salamence came out as two creatures). Prompt = seamless 50/50 blend + the pair's `fuse` text,
  flat 2D Sugimori style; the reference-image route is only the fallback.
- Pokemon fusion look since 2026-11-11 (owner picked "3D + aura" after rejecting text-only, fierce anime, reference-edit
  and plain HD redraws): the design comes from a Pokemon Infinite Fusion community sprite (fusioncalc.com,
  `/sprites/spritesheets_custom/<head>/<head>.png`, 96 px tiles, 20 a row, tile = body id; gen 1-2 ids = national dex),
  redrawn by the Qwen-Image-2.1 Space as a 3D render with a glowing aura (`.cache/if_hd.py <head.body> ...`, ZeroGPU -
  run it after the day's evolution run; a lighter-aura version was tried and dropped). Video (`.cache/if_video.py
  <head.body> <a> <b> <Name> "<aura colour>"`, preview only): parent stills, drawn fusion, 3 s still of the cut-out
  creature, then ONE Wan 2.2 clip of it through HF credits (480p + `hd_clip`, ~$0.20, ~7 min) - the owner wants the fused
  creature really animated ("uska animation nahi rakha"). The Wan prompt keeps the body in place and moves only the
  aura: "rears up and powers up" stood four-legged creatures upright or melted them (4 of 9). Check a frame strip of
  every clip. Pairs carry `"if_sprite": "<head.body>"` in `fusion_pairs.json`; their description credits the Infinite
  Fusion community. Sprites that are only a recolour (Suicune + Entei) or tangled (Kingdra + Gyarados) are skipped.
- `backend/scripts/specs/mewtwo_stones.json` + `scripts/stone_forms.py` (owner idea 2026-11-09): Mewtwo punches an
  evolution stone (`fal_video.punch_clip`), evolves into an elemental form, then a Mega form, each doing a type move.
  Fan-made "What If" (title + narration + description say not in the games). Stone + forms drawn text-only via HF
  credits, cached in `media_output/pokemon/stone-mewtwo-<key>/`. Build: `stone_forms.py fire`; post an approved one:
  `stone_forms.py fire --post` (MEDIA_OUTPUT_DIR = run folder, then merge_state). 8 stones queued; not scheduled.
  Owner rejected the first, locally animated sample ("animation kaha he, proper punch") - motion is now 5 Wan 2.2
  clips a short through HF credits (`fal_video.provider_i2v`, fal-ai, 720p 32 fps, ~2-3 min each; fal's end_image_url
  gave no morph, so each new form is revealed by a REVERSED "pillar swallows it" clip). HD (owner: "clarity HD
  bilkul"): `fal_video.hd_clip` = RIFE v4.6 to 30 fps + Real-ESRGAN animevideov3 x2 -> 1080x1920 CRF 16 (ncnn-vulkan
  tools in `F:/AI AGENT/tools`, laptop RTX 3050, ~2 min a clip; owner approved running them 2026-11-09).
  Final videos are written at CRF 17 (was x264's default 23).
  Stones (2026-11-10): AI-drawn stones came out with little bodies/legs or were cut away by rembg, so the 7 others use
  the OFFICIAL item art (PokeAPI `sprites/items/dream-world/<x>-stone.png`, ~90 px; Ice Stone only has the 30 px
  sprite) upscaled with Real-ESRGAN `realesrgan-x4plus-anime`, alpha taken from the sprite itself (rembg erases
  gems). Every first form wears armour (prompt text in `art()`); never put meta notes such as "looked naked" into a
  prompt - the provider's content check refused the Water form for it. Owner 2026-11-10: the forms looked cute -
  "rage dikhna chahiye": every form and Mega uses `stone_forms.FIERCE` (angry glaring eyes, battle stance, muscular,
  NOT cute); all 8 were redrawn and approved on a sheet before any Wan clip was made (art first, animation second -
  saves credits). Credits: a 720p stone short = 4-5 Wan clips x $0.40 ($0.08/video-second); `STONE_RES=480p` halves
  it (owner chose 480p for Ice/Moon/Dusk/Shiny on 2026-11-10 when credits ran low; hd_clip upscales to 1080p).
  `.cache/wan_fetch.py <request_id> <out>` saves a fal job whose process was stopped (it is billed anyway).
  All 8 approved and queued 2026-11-10 (post ids `custom:stone-mewtwo-<key>`, `.cache/post_one.py stone:<key>`), plus
  a 5:20 long compilation (`.cache/mewtwo_compilation.py` -> `stone-mewtwo-all`, intro of all 8 Megas + a title card
  per stone, chapters in the description; YouTube + gaming Facebook only, `post_one.py stone:all`). Playlist
  "Mewtwo What If - Stone Evolutions" (`youtube_playlists.py` key `mewtwo`).
- `backend/scripts/specs/pal_fusion_pairs.json` - Palworld Pal fusions (owner request 2026-11-09, 1 a day once the
  first sample is approved; `--max-pal-fusions`, default 0). Same pipeline as Pokemon fusions (`FAMILIES` in
  `pokemon_daily.py`): each pair has a part-by-part `fuse` text (checked against the art) and `types` = the parents'
  elements. Art: Pal wiki art (`fetch_pal_art`), cached as `media_output/pokemon/art/pal_<name>.png`. Records/post
  ids `palfusion-<a>+<b>`, playlist "Palworld Fusions", own joke pool. **2026-11-09 the owner called the Pal fusions
  "bohot bakwas"** (the last 6 of the batch were not posted) - make no more Pal fusions until the owner says what to change. Aniimo fusions were built and then dropped
  by the owner the same day.
- `backend/scripts/specs/dbz_fusion_pairs.json` - canon Dragon Ball fusions (owner request 2026-11-09, Toei claim
  risk accepted): Gogeta, Gotenks (Fusion Dance), Vegito, Kefla, Kibito Kai, Fused Zamasu (Potara) - who fuses
  and how checked on Wikipedia's "List of Dragon Ball characters". No free official art: the two characters
  (`art/dbz_<name>.png`) and the fusion are drawn by Qwen-Image-2.1 as fan art; the fusion prompt names the canon
  character. Narration says "Fu... sion... HA!" (dance) / "The Potara earrings are on!". `--max-dbz-fusions`
  (default 0), records `dbzfusion-<a>+<b>`, playlist "Dragon Ball Fusions", joke pool `dbzfusion`. Every Qwen
  draw uses a random seed (a fixed seed redrew the same failed Snorcanine).
  Parent art: drawn characters kept failing (owner 2026-11-09: Jiren purple, then a chibi alien - "bohot bakwas";
  Cell looked like Goku; Frieza pale green), so parents now come from the Dragon Ball Wiki's official renders
  (dragonball.fandom.com API, e.g. `File:Jiren.png` upscaled x4 with Real-ESRGAN `realesrgan-x4plus-anime`, `File:DBS
  Broly Frieza Render.png`) saved as `media_output/pokemon/art/dbz_<Name>.png` - Toei claim risk accepted by the
  owner. `DBZ_LOOK` descriptions are only the fallback when no render is saved. Gokiren's fused art = the official
  Jiren render edited with Goku's hair (`_provider_edit`). A re-made video whose first upload the owner deleted is
  posted as `<pair_id>@v2` by `.cache/post_one.py` (post id + "-v2"). Since 2026-11-09 the attack itself is a Wan 2.2 clip through HF credits
  (`dbz_attack_video`, `ATTACK_MOTION` per attack, HD via `fal_video.hd_clip`) - the drawn version below was "bohot
  bekar"; it stays only as the fallback.
  Each pair has an `"attack"` (`name`, `say` = what the narration shouts, `color`): style 9 makes it the last move -
  `signature` = ball charged in the hands, then the beam fires straight AT the camera (owner 2026-11-09: the old side
  `beam` went off to one side and looked wrong), or `blast` for ball attacks (Eraser Cannon); the joke moves to the closing line.
  Fan fusions on request (`"fan": true` - Gokkolo, Gokhan, Piccorus): titled "Fan Fusion | What If" and the
  description says it is NOT in the anime. Pair ids for Dragon Ball = the fusion's name (Goku + Vegeta is both
  Gogeta and Vegito), `pokemon_daily.pair_id`.
  Gogeta's first Qwen-2.1 draw had no Metamoran vest: its `fuse` text now spells out the Super: Broly design (open
  black vest, yellow padding, teal sash) and the approved art came from HF Inference Providers (`fal-ai/qwen-image`
  via router.huggingface.co, billed to the HF credits - these DO work with the token's Inference Providers
  permission, unlike ZeroGPU over-quota) when the ZeroGPU quota was gone.
- Fusion art when the ZeroGPU quota is used up (`_qwen_draw(provider_ok=...)`): HF Inference Providers billed to
  the HF credits - Pokemon / Pal fusions via `fal-ai/qwen-image-edit-2509` (both parents as `image_urls`, ~18 s,
  first test matched the owner's Lucarace reference), Dragon Ball via `fal-ai/qwen-image` text-to-image. 3 tries
  on provider errors (a transient 422 once). 2026-11-09: owner asked for 10 Pokemon + 10 Palworld + 10 DBZ fusions
  uploaded without asking - built by `backend/.cache/batch_build.py`, every one checked on a frame strip first.
- Fused art cutout: `pokemon_daily._cutout` = rembg 2.0.59 + onnxruntime with the "isnet-anime" model in
  `backend/.cache/u2net` (laptop venv only; the model came from huggingface.co/skytnt/anime-seg `isnetis.onnx`,
  same md5 as rembg's GitHub release, which 504'd). Falls back to `_key_flat` where rembg/model are missing (CI).
- `backend/scripts/specs/games/gta6_13..22_countdown-NN.json` - GTA 6 countdown (owner request 2026-11-09; GTA 6
  launches 19 Nov 2026 and the owner streams it that day): one English short a day 9-18 Nov, `"post_on"` = its IST
  date (daily_job skips a dated spec on any other day, so "5 days to go" never goes out late). Posted by
  `daily-content.yml` (`--extra-slot gta6:1`). Facts: Wikipedia "Grand Theft Auto VI"; the preload date is left
  out (sources disagree), the 3x-RDR2 map size is labelled reported. Older gta6_01-12 specs (Hindi, Oct 2026) are
  all posted. After the launch stream: clip it like the RDR2 streams (own stream; mute/skip licensed radio songs).
- `backend/scripts/specs/gameplay/<plan>.json` - cut plans for the owner's own stream recordings.
- `daily_job.py` picks the lowest-`order` unposted, unexpired spec per slot (`--from-end` takes the
  highest, for manual batches that must not collide with scheduled runs).

### Main scripts
- `scripts/daily_job.py` - the one entry point: retries pending uploads, then builds + posts per
  slot (`--new-games`, `--classic-games`, `--new-palworld`, `--extra-slot slot:N`, `--long-videos`,
  `--long-category`, `--long-series`, `--deals-shorts`, `--deals-long`, `--skip-trends`, `--from-end`). Exits 1 if every build or every
  upload failed (health check).
- `scripts/game_videos.py` (shorts from specs; scenes with a `steam:` image play the game's official Steam trailer
  footage in the picture area - silent, 720p, cached in `game_art/trailers/` and in the workflows' actions/cache;
  owner found still-image shorts cheap, Nov 2026; `"footage": false` opts a spec out). Palworld slots never get
  trailer footage (it showed other Pals): their `pal:` scenes - and `steam:` scenes, with the spec's first Pal - play
  that Pal's art animated by Wan (`pal_motion`, HF_TOKEN, 5 s forward + back, cached per Pal in `game_art/pal_motion/`;
  quota used up -> still), `scripts/long_video.py` (long videos),
  `scripts/palworld_breeding_videos.py` (breeding cards: intro + one card per combo + child; every Pal on a card is
  animated by Wan in a soft oval spotlight under the text layer - `game_videos.spotlight_card`, up to 3 cached moves per Pal).
- `scripts/gameplay_clips.py` (`scan` / `build` / `upload`) - cut Shorts (9:16) and highlight videos from
  recordings in `F:/AI AGENT/VIDEOS GAMING`; `scripts/gameplay_daily.py` (laptop task) finishes pending
  plans, max 1 long + 8 shorts per run, with a lock file. Plans with `"cloud": true` are only rendered there and
  pushed by `scripts/gameplay_cloud.py push` to an orphan branch **`clips/<plan>`** (files > 45 MB split, plus
  `posted.json` = what the laptop already posted); `daily-gameplay.yml` posts them (`gameplay_cloud.py post`) and
  deletes the branch when the plan is done. `.pushed` in the plan's `gameplay_out` folder stops a re-push.
  Pushes go in ~300 MB steps (one 2 GB push got HTTP 500), `posted.json` last (CI skips a branch without it), and
  at most `MAX_ON_CLOUD` = 2 plans sit on GitHub at once (2-3 GB each) - the laptop task pushes the next later.
  The RDR2 recordings were deleted on 2026-11-12 (owner request, 49.6 GB) after every clip of all 15 plans was
  checked as rendered (592 shorts + 41 long in `gameplay_out/<plan>/`): `gameplay_daily.rendered(plan)` lets a
  fully rendered plan be pushed / posted without its recording. The rendered clips are now the only copy -
  never delete a `gameplay_out/<plan>` folder before its plan is fully posted (the streams can be re-downloaded).
- `scripts/rashifal.py` - Hindi daily rashifal (owner request Nov 2026; main FB Page "AI Trend Poster" only, no new
  channel). Since 2026-11-11 ONE video a day with all 12 rashis (~3.5 min: intro card with the 12 symbols, ~13 s per
  rashi, chapters in the description, post id `custom:rashifal-<date>-all`); `--separate` = the old 12 shorts. Each
  rashi shows its symbol picture (ram, bull, twins, crab... `backend/assets/rashi/NN.jpg`, drawn once with
  `.cache/rashi_art.py` through HF credits - owner: "proper symbols, jaise kark ka crab"). Moon rashi/nakshatra/tithi computed with skyfield + JPL DE421 (`.cache/skyfield`,
  Lahiri ayanamsa); reading = traditional Chandra gochar house from each rashi; says "for entertainment", no fear,
  health, money advice or religious messaging. `daily-rashifal.yml` (20:00 IST) makes the next day's 12 and
  schedules them 06:00-06:55 IST; `FESTIVALS` = only panchang-confirmed dates. The CI run failed every day 7-11 Nov
  (no rashifal from CI after 6 Nov; 11 + 12 Nov were made on the laptop): it died 2 s into rashifal.py - the workflow
  never downloaded the Hindi fonts (added 2026-11-11, plus a retried ephemeris download); the edge-tts retry alone
  did not fix it. CI logs need a login, so check step timings through the public jobs API. **Owner rule 2026-11-10: rashifal is posted directly,
  never shown/asked first** (unlike fusions).
- `scripts/youtube_playlists.py` - puts every upload made BY THIS PIPELINE (a record in the posting store) into its
  category playlist on both channels (Pokemon Fusions, Pokemon Evolutions, RDR2 Shorts / Story Highlights, Palworld
  Breeding / Guides, Aniimo, Deals, DML, Classic, New Games, Gaming History; movie slots + Safar / Explained on the
  Hindi channel), creating missing playlists, newest on top. The owner's own older uploads and streams (no record)
  are never touched (owner rule 2026-11-08). Every posting workflow runs it after its uploads (`continue-on-error`).
  Needs the full `youtube` scope - `youtube_login.py <channel> --force` once, then update the GitHub token secret.
  Early RDR2 clips are only in the laptop's store: backfill with `--extra-store media_output/pipeline_store.sqlite3`.
  **Quota**: the Google project has only ~10,000 units/day (resets 13:30 IST) for BOTH channels and all uploads; a
  playlist item costs 50, so each run adds max 8 (newest first). Never backfill in bulk - 160 adds on 2026-11-08
  used the whole day and the day's YouTube uploads failed (retried next day). Daily playlist creation is capped too.
- `scripts/ops_report.py` - token check + weekly report. `scripts/meta_gaming_login.py` - sets up the
  gaming page token. `scripts/youtube_login.py` - YouTube OAuth.
- `scripts/fal_video.py`, `motion_daily.py`, `lucario_*.py` - AI-motion experiments (Wan/Kling/Flow).
- `scripts/fusion_inbox.py` - inbox clip -> 9:16 short (blurred fill, names, fusion name + PokeAPI type, outro),
  posts to the gaming platforms, lists fully posted clips so `fusion-inbox.yml` deletes them from `inbox/`.
- `scripts/pokemon_daily.py` - free Pokemon evolution shorts on CI: official artwork start frames + free Wan 2.2
  (HF ZeroGPU on an HF PRO account, `HF_TOKEN` secret, 4 clips per short, max 3 new shorts a run - `--max-shorts`). Clips persist on the **`motion-cache`**
  branch between runs; finished shorts post to the gaming platforms (`kind: "motion"` in game_videos.json,
  retried by pokemon_daily.py itself, not by daily_job).

### Schedules (IST) - GitHub Actions run ~6-7 h late in practice (Nov 2026)
| When | What |
|---|---|
| 07:00 | Routine **Daily Aniimo + Palworld writer** (`trig_013wepkkQJoFvhUYENfH3qro`) writes specs |
| 07:30 | `token-check.yml` - YouTube + Meta login check |
| Mon/Thu 08:00 | Routine **Weekly content refill** (`trig_01LRixiDvLT8uP4GPUjYnDcy`) tops up all other queues |
| 10:00 | `daily-content.yml` - 1 new game, 3 classic, 1 breeding, 1 Aniimo, 1 movie new, 1 review, 1 reel |
| 10:00 | Laptop task `daily_job.cmd --skip-gaming` (trends) + `motion_daily.py` + `gameplay_daily.py` |
| 11:15 | `daily-pokemon.yml` - Pokemon shorts (HF PRO): up to 3 evolution a day (+ 2 fusion when `--max-fusions` 2; paused 2026-11-08) |
| on push + 12:15 | `fusion-inbox.yml` - fusion clips uploaded to `inbox/` (daily run = retries) |
| 13:45 | `daily-anipal.yml` 1st half - 5 Aniimo + 2 Palworld guide + 3 breeding + 1 long (palworld) |
| 14:00 | `daily-palguide.yml` - 1 Palworld guide |
| 16:00 | `daily-deals.yml` - up to 10 free-game / deal shorts + 2 long deal roundups (Epic + Steam, live) |
| 18:00 | `daily-long.yml` - 2 long gaming + 1 long movie |
| 19:00 | `daily-gameplay.yml` - 1 long + 8 shorts of own-stream clips from `clips/*` branches (laptop renders + pushes) |
| 20:00 | `daily-rashifal.yml` - next day's 12 Hindi rashifal shorts, scheduled 06:00-06:55 IST on the AI Trend Poster FB Page |
| 21:00 | `daily-anipal.yml` 2nd half - 5 Aniimo + 3 Palworld guide + 2 breeding + 1 long (aniimo) |
| Sun 19:30 | `weekly-report.yml` - stats issue |

All gaming workflows share the `daily-gaming` concurrency group (never two writers of `gaming-state`).

## Decisions and owner rules (do not break)

- **Routing**: gaming never goes to the movie channel/page and vice versa. Gaming = English, movies = Hindi.
- **Store deals**: the store APIs are the source (no two-source check), prices are Steam India INR, and
  every description says when it was checked. Deals under 30% off are skipped unless the store features them.
- **Copyright**: never clip third-party films, TV shows or other people's videos (refused: Haunted 3D, TMKOC).
  Offer Wikipedia-verified facts reels instead. Gameplay cuts only from the owner's own streams
  (check the title exists on HarryBeast first).
- **Fact-checking**: every fact needs two independent sources (or the subject's Wikipedia article for
  evergreen facts). Palworld facts must be 1.0-specific. No politics, religion messaging, adult content.
- **Dragon Mania Legends breeding**: no combination is guaranteed in DML, so shorts give the elements the parents
  must carry together (checked on the DML wiki + TouchTapPlay; Cyberly copies TouchTapPlay and does not count).
  Breeding times/prices are wiki-only and stay out. Both wikis sit behind Cloudflare - use web search snippets.
- **Palworld breeding**: every short shows **3 verified combos** (round 2: **5 new combos**) for the same child (checked on
  PalBreedCalc + pindrop / the breeding formula). Child must differ from all parents, no variants,
  no legendary/crossover parents, no gender-dependent results.
- **Humour** (owner request 2026-11-08): every video has ONE light, on-topic joke (`scripts/humor.py`). Spec writers
  add a `"joke"` field to each new short / long spec: one short sentence in the video's language about its own topic
  (the game, the Pal, the deal, the movie-going experience), never about a real person, group, body, religion or
  politics, nothing adult, no insults, no fear/money/health advice, never a fact claim. `"joke": ""` = none. Specs
  without one get a line from the slot's pool; tributes and `movie_news` get none. The joke goes just before the
  closing question (`humor.insert`); fusion/evolution shorts say it over the last clip / a free closing still,
  rashifal after the tip, long videos in the last section.
- **Tags**: YouTube tags are filled up to the 500-character limit (`fill_tags` in `app/social/youtube.py`).
- **Instagram cover**: Reels use `thumb_offset` 2000 ms (`INSTAGRAM_THUMB_OFFSET_MS`) - frame 0 is the
  black fade-in.
- **Titles**: no `.` or `?` (YouTube title is cut there), no `<` or `>` anywhere (YouTube 400).
- **AI Trend Poster Page volume** (2026-11-11): the Page had 0 followers and 0 views on its last 100 videos with ~19
  uploads a day (12 rashifal + 3 movie + laptop trend-news). Owner chose: max ~4 a day - 3 movie shorts + ONE combined
  rashifal; the laptop trend-news videos are off (`daily_job.cmd` passes `--skip-trends`). The token has no
  `read_insights`, so only the Graph `views` field is available. The gaming Page (790 followers) also shows ~0 views.
- **Movie shorts look** (owner approved 2026-11-11): full-screen "cinema" style, `scripts/cinema_video.py` (big framed
  picture that slides in and zooms, hook line over the first 1.7 s, white flash on cuts, big fact text, grain +
  vignette). `game_videos.build_video` routes every `category: movies` spec there; `"style": "card"` keeps the old look.
- **Volume**: owner wants 20 Aniimo/Palworld shorts + 2 long a day on top of the regular schedule.
  Reach data (Oct 2026): classic games ~207 avg views, Aniimo ~193, Palworld ~97, long videos 40-80;
  a 60-upload day gave 2-5 views each, so gameplay uploads are capped per day.
- **Gaming page back catalogue**: videos made before 2026-10-21 have `skip:before-gaming-page` markers
  for `facebook_gaming`/`instagram_gaming` - do not remove them (would dump hundreds of old videos).
- **Machine**: keep every install/cache on **F:** (C: is nearly full). `ollama.exe list` with no server
  running starts the Ollama app on C: - start `ollama.exe serve` with `OLLAMA_MODELS` set instead.
- **Secrets**: only in `backend/.env` (gitignored) and GitHub secrets. Never print or commit tokens.
- **The repo is PUBLIC** (since 2026-11-09): as a private repo it used up the 2,000 free GitHub Actions minutes a
  month and every run failed in 3 s from 2026-11-07 ("recent account payments have failed or your spending limit
  needs to be increased") - public repos get Actions free. History was scanned clean before. So: code, specs,
  `gaming-state`, `clips/*` and Actions logs are world-readable - never commit a token, cookie or private file,
  and never add a `pull_request`/`pull_request_target` trigger (outsiders could run workflows).
- **Process**: confirm before running tests. Posting state is only ever merged into the latest `gaming-state`
  with `scripts/merge_state.py` (every workflow's save step does fetch + merge + retry; laptop batches too) -
  never copy a state file over it: a copy + push from a stale base once lost a whole day's records.
  Laptop batches use `--from-end` order so they don't pick the specs a scheduled run is about to take.
- **YouTube quota** resets at midnight Pacific (~12:30 IST); morning IST uploads count to the previous day.

## What is built

- Daily gaming + movie shorts, reels, long videos, Palworld guides, Aniimo, breeding (3-combo) - all
  on GitHub Actions, posting to YouTube (2 channels), 2 Facebook Pages, 2 Instagram accounts.
- Daily Aniimo + Palworld pipeline (writer routine + `daily-anipal.yml`).
- Daily free games + store deals (`daily-deals.yml`, `scripts/store_deals.py`) - 10 shorts + 2 long a day.
- Pokemon evolution shorts (`daily-pokemon.yml`, `scripts/pokemon_daily.py`) - up to 3 a day (HF PRO), 50 lines queued;
  plus up to 2 automatic fusion shorts a day (`--max-fusions`, Qwen-Image-2.1 art, parent stills + drawn fusion, fused creature animated locally).
  Charmander evolution got strong views (Nov 2026) - owner bought HF PRO to scale it.
- Pokemon fusion shorts from Gemini clips (`fusion-inbox.yml`, `scripts/fusion_inbox.py`) - the clip itself is made by hand.
- Weekly refill routine, login check, weekly stats report, health-check exit codes.
- Gameplay clipper for own RDR2 streams (done: Ch1 P1, Ch2 P1-P3; ~220 clips posted; Ch2 P4
  2026-11-02 - 50 shorts + 3 long, first plan posted from CI via `clips/rdr2_ch2_p4`). Before planning a recording, match its duration to the stream on
  HarryBeast - the owner once re-added an already-clipped part. Plans written 2026-11-03: Ch2 P5, Ch3 P1-P3;
  2026-11-08: Ch3 P4, Ch4 P1-P3, Ch5 P1, Ch6 P1-P4 + P6 (400 shorts, 25 long; every stream downloaded is now planned).
  Titles come from the scan grids (`gameplay_out/<plan>/scan/grid*.jpg`); scenes with hate-group imagery and slur or
  profanity quotes are left out of titles/clips. Streams were fetched with yt-dlp (in the venv, laptop only, their own
  HarryBeast streams, 1080p60, `-f 299+140`, ffmpeg from imageio-ffmpeg) into `VIDEOS GAMING`.
- Laptop trend pipeline (Ollama + SD) and free AI-motion queue (`motion_queue/` -> `motion_videos/`).

## Known limits / not done

- Cloud cannot read the laptop's `VIDEOS GAMING` folder; YouTube blocks yt-dlp from GitHub runners.
  A Google Drive route was offered and declined - scanning, planning and rendering stay on the laptop; only the
  posting of rendered clips moved to CI (`clips/*` branches).
- No working paid image/video model (Pollinations empty, Gemini Veo/image need billing).
- Instagram API cannot change the cover of already-posted Reels.
- Twitter trend source has no token (skipped).
- Pokemon shorts use official artwork (Nintendo / The Pokemon Company IP) - claim risk accepted by the owner.
  Free Wan output is 480p (upscaled) and cannot morph one form into the next - the morph is the drawn evolve clip.

## Change log
- 2026-11-12 - RDR2 stream recordings deleted after all 15 plans were rendered; `gameplay_daily.py` posts rendered plans without the recording; fusion pairs can override shown names (`names`).
- 2026-11-12 - Wikipedia images: 1920 px copy, 960 px retry on a timed-out download (`game_videos.fetch_image`) - no movie long video had been built since 27 Oct; gaming long specs are used up (0 of 56 left).
- 2026-11-11 - Pokemon fusions from Infinite Fusion sprites redrawn as "3D + aura" + one Wan clip of the fused creature (`.cache/if_hd.py`, `if_video.py`, `if_sprite` pairs with credit); 6 approved and queued.
- 2026-11-11 - AI Trend Poster: cinema-style movie shorts (`cinema_video.py`), ONE combined rashifal a day with symbol pictures, trend-news off; fusion compilations (`.cache/fusion_compilation.py`, `post_one.py comp:<family>:<tag>`); Pokemon fusion text-only art only for `text_only` pairs.
- 2026-11-11 - `daily-rashifal.yml` downloads the Hindi fonts + ephemeris with retries (it had failed in 2 s every day).
- 2026-11-10 - Facebook resumable upload for big videos (long videos 413'd); edge-tts retries (rashifal CI failed 7-10 Nov); laptop queue (`.cache/post_queue.py`) also posts `rdr:<plan>:<n>` own-stream shorts and `pal:<key>` breeding shorts built by `.cache/build_breeding.py`.
- 2026-11-09 - Pokemon fusion art is text-only (`fal-ai/qwen-image`, no parent pictures) - a real blend, owner approved; Mewtwo + stones shorts (`stone_forms.py`), Fire sample built.
- 2026-11-09 - Fusion art drawn through HF Inference Providers (credits) FIRST, ZeroGPU only as fallback: laptop fusion art had used up the shared ZeroGPU day, so no Wan evolution short was posted 5-9 Nov.
- 2026-11-09 - Fusion style 9: Dragon Ball pairs fire their signature `attack` (Kamehameha, Final Flash...) at the camera; Pal fusion batch rejected by the owner.
- 2026-11-09 - Fusion prompt: one face, exactly two eyes (no second eye pair / face); every fusion shown before upload.
- 2026-11-09 - `fal_video.build` deletes its raw RGB frame caches (~1 GB a clip) after writing final.mp4 - 42 fusion folders had filled F: (204 GB).
- 2026-11-09 - GTA 6 countdown shorts (10, dated with `post_on`, daily-content posts 1 a day).
- 2026-11-09 - Fusion style 8: two type/character-based moves per fusion (10 local moves); DBZ fan fusions; Gogeta posted.
- 2026-11-09 - rembg isnet-anime cutout for fused art; Gogeta drawn via HF Inference Providers (credits); jump height capped for tall characters.
- 2026-11-09 - Fusion style 7: 3 s still, then local leap-and-slam + energy-blast actions; Gogeta/Gotenks vest described (open vest, yellow padding).
- 2026-11-09 - Canon Dragon Ball fusion shorts (6 pairs, Qwen fan art), random Qwen seeds.
- 2026-11-09 - Fused art from Qwen-Image-2.1 (realtime Space): new standing character per pair, approved by the owner.
- 2026-11-09 - Actions on Node 24 (checkout@v5, setup-python@v6, cache@v5, upload-artifact@v6); runners pinned to ubuntu-24.04.
- 2026-11-09 - Workflow downloads (Hindi fonts, cloudflared) retry 5x; fonts fall back to jsDelivr; a cloudflared failure only warns.
- 2026-11-09 - Fusion style 6: 2 s still of the fused creature, then local idle/power-up animation (no Wan).
- 2026-11-09 - Fused art from the Qwen Super-Fusion LoRA Space + flat-background cutout; no Kontext fallback for `fuse` pairs.
- 2026-11-09 - Palworld fusion shorts (12 pairs, official Pal art), generic fusion families in `pokemon_daily.py` (Aniimo dropped).
- 2026-11-09 - Repo made public: private Actions minutes ran out on 2026-11-07 (no CI run worked 7-8 Nov).
- 2026-11-08 - YouTube playlists per category (`youtube_playlists.py`, run by every posting workflow); `youtube` OAuth scope added.
- 2026-11-08 - Fusion art from Qwen-Image-Edit-2509 with both parents + part-by-part `fuse` text (23 pairs); livelier Wan prompts; auto fusions paused until approved.
- 2026-11-08 - Fusion style 4: the two parents as stills, drawn fusion, only the fused Pokemon animated by Wan (owner request).
- 2026-11-08 - RDR2 plans for all remaining streams (Ch3 P4 - Ch6 P6, 400 shorts + 25 long), rendering on the laptop.
- 2026-11-08 - Humour: one on-topic joke per video (`humor.py`, spec `"joke"` field, slot pools); Dialkia + Lucarace fusions.
- 2026-11-06 - Fusion style 2: one-parent Kontext edit, locally drawn fusion transition, body-motion Wan prompts (owner: animation looked bad).
- 2026-11-06 - `daily-rashifal.yml`: 12 rashifal shorts every day on the AI Trend Poster FB Page, scheduled for 06:00 IST.
- 2026-11-06 - `rashifal.py`: 12 Hindi rashifal shorts (Dhanteras, 6 Nov) posted to the AI Trend Poster Facebook Page.
- 2026-11-04 - Breeding cards with animated Pals (oval spotlights); 3 Wan moves per Pal so scenes do not repeat.
- 2026-11-04 - Palworld shorts: the short's own Pal animated by Wan (cached per Pal) instead of generic trailer footage.
- 2026-11-04 - Shorts play official Steam trailer footage instead of still screenshots (Aniimo, Palworld guide, new games).
- 2026-11-04 - Pokemon evolution style 2: game-style evolve transitions + white flash, Mega Evolutions; Charmander -> Mega Charizard X/Y first.
- 2026-11-03 - `daily-gameplay.yml` + `gameplay_cloud.py`: rendered own-stream clips go to `clips/<plan>` branches and post from CI (8 + 1 a day).
- 2026-11-02 - Palworld breeding round 2: 168 children queued again with 5 new combos each (`key` in the queue); 10 Aniimo specs.
- 2026-11-02 - Automatic Pokemon fusion shorts in `pokemon_daily.py` (FLUX.1 Kontext fused art + Wan), 2 a day, `look` per pair.
- 2026-11-02 - HF PRO for Pokemon evolution shorts: max 3 a day (`--max-shorts`), queue 20 -> 50 lines (PokeAPI-checked).
- 2026-10-30 - Dragon Mania Legends breeding: 10 shorts (`dml` slot) + 2 long (`series: dml`), official Play Store art.
- 2026-10-29 - `fusion-inbox.yml` + `fusion_inbox.py`: Gemini fusion clips in `inbox/` become posted 9:16 shorts.
- 2026-10-29 - `daily-pokemon.yml` + `pokemon_daily.py`: free Pokemon evolution shorts posted from CI (motion-cache branch).
- 2026-10-29 - `fal_video.py`: free Pokemon start frames (`poke`); first free Wan evolution short queued (Charmander line).
- 2026-10-25 - Workflows save state via `merge_state.py` (fetch + merge + retry); classic 3/day; MW4, Aion 2, GTA 6, Minecraft + 6 classic specs.
- 2026-10-24 - Daily free games + Epic/Steam deals: `store_deals.py`, `daily-deals.yml`, `url:` image refs.
- 2026-10-24 - CLAUDE.md created; Aniimo + Palworld daily pipeline (writer routine, `daily-anipal.yml`, `--long-series`).
- 2026-10-22 - Instagram Reel cover offset; 10 classic + 10 breeding batch.
- 2026-10-21 - Gaming Facebook Page + Instagram; daily long = 2 gaming + 1 movie; tags to 500 chars.
- 2026-10-20 - Palworld breeding shows 3 combos; Aniimo back (1/day); classic 2/day; gameplay daily caps.
