# CLAUDE.md - HarryBeast content pipeline

> **Keep this file current.** After every feature, workflow, schedule or rule change, update the
> matching section below (and "Change log" at the end) in the same commit.
>
> **New session? Read `PROJECT_OVERVIEW.md` first** - a short map of the project, its current status, the
> repo layout and how a video goes from spec to upload. Update its "Status right now" section when that changes.

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
  **Owner's own voice** (owner 2026-11-23: "mera voice use karo ... youtube repetitive content use nahi karne deta"): with `OWNER_VOICE=true` (laptop `.env` only) English narration is spoken by `en-IN-PrabhatNeural` at -8% and then turned into the owner's voice by Chatterbox VC (`scripts/owner_voice.py`, MIT, own venv `F:/AI AGENT/tools/voice/venv` on the GPU, reference `F:/AI AGENT/voice/owner_ref18.wav`, ~30 s a call) - `video_gen._to_owner_voice`. VC keeps the length, so edge-tts word timings (captions, cinema cues) stay right. Owner picked this on 2026-11-24 ("1st voice best he"): the cloned voice from Chatterbox's own English TTS sounded "maxican" and too fast. Hindi (owner 2026-11-24: "1st voice for hindi") = Chatterbox multilingual TTS speaking the text itself (`video_gen._owner_voice_hindi`, `owner_voice.py --tts hi`, exaggeration 0.6 / cfg 0.5 like `voice/test_hindi.wav`): one sentence at a time with 0.3 s gaps, so sentence times are exact; words inside a sentence share its time by length (the model gives no word timings). CI has no GPU, so CI-built videos keep the edge-tts voice. Evolution / fusion / stone shorts narrate through `lucario_stone_video._tts` (not video_gen) - it uses the owner's voice too since 2026-11-24 evening (the 6 evolution shorts built at 20:15 that day still had the AI voice).
  edge-tts now and then answers "No audio was received" - `video_gen.generate_video` retries narration 4x.
- **Images**: Wikipedia/Commons (`wiki:`, `wikifile:` - asked for at 1920 px; a download that times out is retried with the 960 px
  copy: one original that never finished downloading failed every movie long-video run 28 Oct - 11 Nov 2026), Steam screenshots (`steam:<appid>:<n>`), Palworld
  wiki art (`pal:<Name>`), official YouTube frames (`yt:`), official Pokemon artwork on a
  background photo for AI-motion storyboards (`{"poke": name, "background": ref}` in `fal_video.py`, PokeAPI). Local Stable Diffusion 1.5 + x4 upscaler
  (`app/media/image_gen.py`, laptop only). Pollinations balance is empty; Gemini image/Veo need billing.
- **LLM on laptop**: Ollama `gemma3:4b` (trend pipeline). Models live in `backend/.cache/ollama-models`.
- **Speech-to-text on laptop**: faster-whisper 1.2.1 (CTranslate2 4.9) on the RTX 3050 (4 GB): `medium`,
  `compute_type="int8_float16"`. CUDA libs come from the venv's `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` wheels
  (installed 2026-11-19, no CUDA Toolkit) - add `site-packages/nvidia/*/bin` with `os.add_dll_directory` + PATH before
  loading the model (see `gameplay_out/gta6_day2/asr.py`), else "cublas64_12.dll is not found". CPU fallback: `small` int8.
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
  Owner 2026-11-17 ("jab bhi evolution aaye pokemon ka to mega, dynamax and gigantamax sab dal dena, agar nahi he tab bhi
  aap bana dena" - the Chimchar short had no Mega): every evolution short now ends with Mega -> Dynamax -> Gigantamax
  (`pokemon_daily.with_forms`). Official Megas / Gigantamax come from PokeAPI (`megas_of`, `gmax_of`); when a line has
  none, `fan_form` draws a FAN-MADE one from the official art (`fal-ai/qwen-image-edit-2509` via HF credits, ~$0.04, no
  ZeroGPU; cutout with rembg; cached `<run>/pokemon/art/fan_<mega|gmax>_<slug>.png`) and the narration, title ("What If")
  and description say it is fan-made / not in the games. Dynamax is drawn locally (`fal_video.dynamax_clip`: red
  energy, grows giant, red clouds, shake); the Gigantamax change is the evolve clip with a red `tint`, then a Wan clip.
  Each line now needs 1-2 more Wan clips (fan Mega + Gigantamax), so fewer lines fit in a ZeroGPU day.
  `"forms": false` in a queue entry turns it off; a failed fan drawing just leaves that form out.
  Fan art rules learned on Groudon (2026-11-17): fierce look (`FAN_FIERCE`, owner: "thoda aur khaarnaak banao"), but
  keep the reference colours (`FAN_KEEP` + `_main_colour` names them - "dark armour" turned Groudon black, "ground-type
  structures of sand" turned it beige), solid white background (no clouds / ground / checkerboard - the red clouds come
  from the Wan prompt), cutout with the white flood fill (`_key_flat`; rembg left grey patches). Check a sheet of the
  art BEFORE the Wan clips are made. Primal Reversion (Groudon / Kyogre, PokeAPI `-primal`) comes before the Mega;
  Pokemon that do not evolve get "<Name> Transforms" titles. `WAN_VIA_CREDITS=480p` makes the Wan clips through HF
  credits (~$0.20 a clip) instead of ZeroGPU - used for Groudon "abhi" on 2026-11-17, owner chose credits.
  Faces (owner 2026-11-17: "dragonite ka chehera bohot bekar he animation me bhi"): a "draw its Gigantamax form"
  prompt gave Dragonite a Charizard face three times, so the fan Gigantamax is an ADD-ONLY edit of the official art
  (keep the picture, only add red cracks / black spikes / red flames / glowing angry eyes); Mega and Gigantamax Wan
  prompts say "mouth closed, face keeps exactly the same shape" - "roars" made Wan open a huge toothed mouth.
  Dynamax vs Gigantamax (owner 2026-11-20: "Dynamax and Gigantamax me farak he to har videos me alag alag dikhana
  honga, jiske he uske same nahi to banao"): Dynamax = the same body, giant, RED aura + red clouds (local clip).
  Gigantamax = the official G-Max form when PokeAPI has one, else a fan form whose BODY changes (giant crown / mane /
  wings / tail of the type's energy, energy rings; face kept) in the TYPE colour (`TYPE_TINT`, never red) - the old
  add-only red-cracks edit looked like the Dynamax. Fan Mega = an edit too (armour, spikes, own colours; drawing a new
  picture lost Hydreigon's hand-heads and added dust). Cache `fan_gmax_v2_<slug>.png`. Fan art: HF credits first, the free
  ZeroGPU Space when they are gone (credits empty since 17 Nov - Axew/Bagon/Deino of 20 Nov had no fan forms); a cut
  that leaves the border solid falls back to rembg. A form that cannot be drawn now FAILS the line (retried next run)
  instead of posting it without. `PARTS` adds body-part notes per Pokemon. Mega Hydreigon failed 6 draws (4-5 heads,
  dust).
  Hydreigon (owner 2026-11-22: "evolution 3 bekar he, end me aate aate haath dikhte he, iska koi gigantamax nahi banaya"):
  Wan turned its hand-heads into human hands and its tail into ghost legs, so `hydreigon` is in `LOCAL_ANIM` (a slug there
  also covers its fan forms, `_local_anim`; the closing still is then drawn from the last local form). `PARTS` now places
  its three heads as in the official art and says it has no legs ("one main head on its neck" gave a 4th head on a long
  neck). Fan Gigantamax v3 drawn and checked (3 heads, flaming wings, glowing crown). Mega Hydreigon failed 9 draws with
  the generic Mega prompt (extra heads / clawed arm / legs / dust); owner: "where is its mega form" - so `MEGA_DESIGN` +
  `FAN_MEGA_ONE` give it an ADD-ONLY Mega edit like the Gigantamax (armour, sharper spikes, red chest gem, silver wing
  edges; cache `fan_mega_v3_<slug>.png`), and `_drop_dust` erases the dust cloud the edit model still draws under it.
  1 of 4 draws had exactly 3 heads - always count the heads. Queue entries can also carry `"skip_forms": ["mega"]` (form
  left out, title / narration / description drop the word Mega) for a fan form that cannot be drawn at all.
  Own Gigantamax per Pokemon (owner 2026-11-21 on Axew / Bagon / Trapinch: "kya sabhi ke gigantamax same rahenge",
  "Salamence ka gigantamax kaha he"): the one gmax prompt buried every Pokemon in the same mane of type-coloured flames
  (Haxorus and Salamence were the same blue blob, Salamence's red wings gone). Now `pokemon_daily.GMAX_DESIGN` gives each
  final stage ONE signature change like the official G-Max forms (Salamence: colossal red wings + cloud cyclone; Haxorus:
  golden axe-blade tusks, back plates, NO hand-held weapon - the first draw gave it two axes; Flygon: sand tornadoes at the
  wing tips) with its own tint colour and Wan motion; prompt `FAN_GMAX_ONE` says nothing may cover the body. Cache
  `fan_gmax_v3_<slug>.png`; Pokemon not in the dict keep the v2 prompt - add a design before their line is built (all queued
  lines up to Kommo-o have one). `POSE` pins the Wan pose: "stands tall" stood Mega Salamence up like a person. Wan broke Salamence and Mega Salamence
  again with the hover pose (humanoid body, ghost rings, a second face), so forms in `LOCAL_ANIM` get the local
  `fal_video.idle_clip` (breathing, aura, particles - the art never changes) instead of a Wan clip.
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
  Launch stream clipped 2026-11-19 (owner's OBS recording `VIDEOS GAMING/2026-11-19 00-00-08.mp4`, 58 min, facecam
  bottom right): plan `specs/gameplay/gta6_launch.json` = 41 shorts (story / action / NPC / graphics, picked from a
  faster-whisper transcript `gameplay_out/gta6_launch/asr.txt` + 10 s frame sheets; menus and crude sexual lines left
  out) + 2 long videos (Lucia's story, Jason's first mission). **NudeNet at 1 fps missed a beach shot (1257-1263 s: NPCs
  in thongs, a bare behind)** - a stricter 2 fps pass (`gameplay_out/gta6_launch/strict_scan.py`, exposed >= 0.25,
  covered buttocks/breasts >= 0.55) caught it; it is in the plan's `excluded` list and cut from short 14 and long 2.
  Run that strict pass + look at the hits for every new open-world recording. Posting: owner approved by judgement
  ("aapke hisab se dal do"), 10 at a time - `.cache/gta6_post.py 1-10` (Instagram light copy + gaming Facebook within
  the 2-a-day cap; YouTube gets `hold:review` during the strike - clear the hold before `gameplay_clips.py upload`),
  state in `run_gta6`, then `laptop_batch.py gta6 --merge-only`.
  GTA 6 day 2 (owner's 2.5 h recording `VIDEOS GAMING/2026-11-19 19-44-31.mp4`, free roam + missions): plan
  `specs/gameplay/gta6_day2.json` = 65 shorts + 5 long videos (free roam / cops, Joey's garage + Brian's rent, the
  impound / Phoenix mission, house repairs, hunting Arnold), picked from 10 s frame sheets (`gameplay_out/gta6_day2/scan.py`,
  one decode pass: 2 fps frames -> sheets + strict NudeNet) - the owner talks Hindi, so the transcript is only a helper.
  `excluded` in the plan: the shower (Jason's bare behind, 0-268 s), beating an NPC woman, Mandy in underwear
  (6540-6810), swimwear at the yacht / pools / neighbors, Arnold shot while kneeling, the drug lab ("collect the acid",
  2880+). `scan_plan.py` checks only the planned parts. Posted with `.cache/gta6_post.py <a>-<b> --plan gta6_day2`
  (same `run_gta6` state, so the gaming Page's GTA 6 cap - prefix `gameplay:gta6_`, 6 a day - counts both plans).
  Shorts 1-20 approved and posted to Instagram 2026-11-19 (Facebook via the 19:30 task after the launch shorts).
  This recording has the game audio TWICE (OBS: two game-audio sources; the copy is 195-265 ms late and ~20-55 %
  per band). A per-band echo filter (`gameplay_out/gta6_day2/fix_audio.py`) removed it, but the owner said "jane do echo
  hone do" - the clips keep the original audio. Tell the owner to mute one of Desktop Audio / Game Capture audio in OBS.
  GTA 6 day 3 (owner 2026-11-20, two recordings, no doubled audio this time): `gta6_day3a` (9:38, 3 shorts - mostly
  settings menus) and `gta6_day3b` (2:51, 72 shorts + 8 long: free roam + bridge jump, gun store + ambush, Cal's JoySystem
  games, the shipwreck dive, errands + haircut, Lucia out of prison, pool + bar fight, the mobility scooter job + Fleeca
  bank). Plan written by `gameplay_out/gta6_day3b/make_plan.py`; every in-plan strict-scan hit was looked at (false alarms:
  shorts, wetsuits, clothed NPCs). `excluded`: bedroom / underwear / shower / towel scenes, the dance party + dating app,
  phone feeds (bikini photos, an offensive meme), bodies after the back-room fight, Dobbs' bloody legs, a man in underwear.
  Not checked against HarryBeast streams yet; post with `.cache/gta6_post.py <a>-<b> --plan gta6_day3b` after approval.
  GTA 6 day 4 (owner 2026-11-21, `VIDEOS GAMING/2026-11-20 21-44-13.mp4`, 2:56, no doubled audio): plan `gta6_day4` = 102
  shorts + 10 long (plane-crash package, gator country, Brian's seaplane deal with Lucia, the parole officer, Pete's escort
  flight, biker hangar shootout, home life + car mods, pool at the Rusty Anchor, beach camp + swamp hideout, Alan and Janet),
  written by `gameplay_out/gta6_day4/make_plan.py`. All 78 in-plan strict-scan hits were false alarms (tank tops, the white
  cue ball, planes). `excluded` also: the coffee-table scene (low camera on Lucia kneeling in sleep shorts - suggestive), bed /
  kiss scenes, the night pool party, Jason shooting a downed biker in a blood pool (5405-5485), bloody bodies, a phone video of
  Janet partying, the owner asking for the strip club on the map, crude radio / subtitle lines. All rendered and strip-checked.
  **The owner found nudity I had not reported**: a topless woman dancing at the night pool party (2:51:30, 10270-10400 excluded) - NudeNet missed the small distant figure and its one hit there was outside the plan, so never opened. Rule since 2026-11-21: open EVERY scan hit of the whole recording and look at every party / pool / beach / bedroom scene on the sheets, then tell the owner each nudity timestamp (even when already cut).
  GTA 6 day 5 (owner 2026-11-21, `VIDEOS GAMING/2026-11-21 15-25-43.mp4`, 2:10, no doubled audio): plan `gta6_day5` = 89
  shorts + 7 long (storm drive + car thief mode, supercar spree + bear protesters, Donnie's lost packages in the swamp, bucket
  truck home, night + morning at home, Skunk Ape + Brian's hangar, the Kingfisher setup mission), written by
  `gameplay_out/gta6_day5/make_plan.py`. All 318 strict-scan hits of the whole file were opened. Nudity / adult spots (all
  excluded): bed + underwear at the start (0-122), the whole Rusty Anchor dock / yacht party / jet skis / nightclub / hotel
  part 4560-6235 (thong bikini ~1:31, nude statue in the club, bare-skin close-up in the club bathroom 1:38:15, sex on hotel
  beds 1:39:20 / 1:40:00 / 1:40:30, bikinis next morning); also cut: a body in the bait shop, a low rear camera on Lucia,
  the phone feed, blood on the airboat. Every short (8 frames) and long (16 frames) strip-checked. Shorts 66-69 + long 6 show
  an NPC knocked into Jason's truck bed and carried to the hangar (no blood) - owner decides. Not posted - waiting for approval.
  Long 3 (~15.5 min) and long 7 (~17.7 min) are over Instagram's 15 min. Approved 2026-11-22 and in the hourly queue.
  GTA 6 day 6 + day 7 (owner 2026-11-22, two recordings): `gta6_day6` (`2026-11-21 20-42-27.mp4`, 2:14 - Donnie's trailer,
  fishing with Cal, the shipwreck dive, the visitor-center setup, the house fire, the hospital escape, Chapter II) = 128
  shorts + 6 long; `gta6_day7` (`2026-11-22 16-03-26.mp4`, 1:28 - Lucia in Port Gellhorn: Vicki, Moses' car jobs, the
  ambulance, Caleb's tractor revenge) = 82 shorts + 4 long. Plans from `make_plan.py` (PARTS = a stretch cut into equal
  shorts). All 201 + 89 strict hits opened (`.cache`-free helper: scratchpad `hitsheet.py` -> `check/hits/hNN.jpg`).
  Excluded nudity: day 6 Jason in underwear (0-262), Lucia in underwear on Jason in bed (5283-5335), underwear in the motel
  bed (7945-7965), a small statue of a naked woman Jason holds (7832-7870, found only on the strip check of the renders); day 7 the Delights strip club with a fully nude dancer (455-750), Caleb's ex-boss naked on the tractor
  (4660-4690), a naked woman in the woods after the alien light (5150-5325). Also cut: blood-pool close-ups, a drugs talk,
  the "Delights Dancers" card menu, close-ups of Lucia's hips in the clothes shop. Both rendered + strip-checked 2026-11-23 (day 6 = 128 shorts + 6 long after the statue cut); every in-plan scan hit was already among the opened whole-file hits. Approved 2026-11-23 ("haan", subtitle swear word / Vicki close-up / pills line kept) and added to the hourly queue (only day6 long 5 and day7 long 1 are under 15 min).
  **GTA VI logo** (owner 2026-11-23: "simple GTA 6 ke text ke jagah ek mast logo", picked design A of two): `gameplay_clips.gta6_logo`
  (white "GTA" + "VI" in a Vice City sunset gradient with a pink glow - our own design, not Rockstar's trademarked logo)
  replaces the red text badge on every short whose plan game is in `LOGOS`. `.cache/gta6_relogo.py` re-renders the shorts not
  on Instagram yet in hourly order (427 on 2026-11-23; `--all` also does the posted ones for YouTube), swaps each file only
  while `hourly.lock` is absent, deletes its stale `.light.mp4` / NudeNet cache, logs to `gameplay_out/relogo.log`.
  Laptop tasks run through `conhost.exe --headless` since 2026-11-22 (owner: "ye kyu khul raha he" - the hourly task
  opened a cmd window every hour).
  **GTA 6 Chapter 1** (owner 2026-11-21: "kya aap editing kar sakte ho youtube ke liye ... ek chapter 1 banao", chose the
  launch stream, intro + chapter cards, zoom + text pop-ups, English subtitles): an EDITED 31:46 story video, not a clip cut.
  Folder `gameplay_out/gta6_launch/chapter1/`: `edl.json` (hook moments, title shot, 13 parts of source segments with
  `pops` = text pop-ups and `zooms` = punch-ins), `edit.py` (renders each piece with NVENC h264 - the RTX 3050 encodes 1080p60
  fast - then concat copy; writes `chapter1.mp4`, `chapters.txt`, `timeline.json`), `ocr_subs.py` (RapidOCR 1.4.4 in the
  venv reads the game's burnt-in subtitles at 2 fps -> `subs_raw.json`; the Whisper transcript of this stream is too poor for
  captions), `make_srt.py` (-> `chapter1.en.srt` for the YouTube caption upload, swear words masked as f*** etc.),
  `description.txt`, `thumb.jpg`. The whole launch recording was strict-scanned (`gta6_launch/full/`): 45 hits inside the
  edit, all false alarms. Not uploaded - YouTube strike; shown to the owner in VLC first.
- `backend/scripts/specs/gameplay/<plan>.json` - cut plans for the owner's own stream recordings.
- `backend/scripts/specs/games/yugioh_NN_<card>.json` - Yu-Gi-Oh card explainers (owner 2026-11-16: "har card ke bare me
  batao english me ki kya he kya karta he and kaise use karna he" + "players ke best cards" videos). Format: what it is
  (type / Level / ATK / DEF), what it does (official card text from the YGOPRODeck API, said simply), how to use it (real
  support cards, their official text). Story facts: Wikipedia + Yugipedia. Images = official card art from
  images.ygoprodeck.com (`url:` refs; Konami claim risk like the Pokemon art); NO anime clips. `"style": "cinema"` +
  `"tts_voice": "en-US-GuyNeural"` (game_videos routes any spec with style cinema to `cinema_video.py`). Built and posted
  only through the review queue: `laptop_batch.py ygo --extra-slot yugioh:N` then `post_reviewed.py`.
  50 specs written 2026-11-16 by `.cache/yugioh_write.py` (44 card explainers + 6 duelist best-card videos: Yugi, Kaiba,
  Joey, Jaden, Yusei, Zane). Egyptian God pairs (owner 2026-11-17: for each God a "deck" video = the support cards, and
  a "how to summon it" video): orders 51-56 (`slifer-deck`, `slifer-summon`, `obelisk-deck`, `obelisk-summon`, `ra-deck`,
  `ra-summon`) written by `.cache/yugioh_gods_write.py` from the official card text, TCG / Master Duel cards only (Holactie
  named as OCG only); built in their own run folder `run_ygo_gods` so the daily `ygo_post.py` drip (reads `run_ygo`) never
  posts them before the owner has watched them. Owner 2026-11-17: several of those cards are NOT in Duel Links (the
  owner plays Duel Links), so 51-56 are titled "Yu-Gi-Oh Master Duel and TCG" and Duel Links versions are orders 57-62
  (`yugioh-dl-*`, `.cache/yugioh_gods_dl_write.py`, run folder `run_ygo_gods_dl`): only cards / Skills Duel Links Meta
  lists (its card API has release + obtain data; YGOPRODeck's "Duel Links" format flag misses newer boxes), Speed Duel
  rules (4000 LP, 3 Monster Zones), and the Ra deck = Duel Links Meta's top deck of 2026-11-15. Always say which game a
  Yu-Gi-Oh deck video is for. All 12 approved 2026-11-17 and posted to Instagram with `.cache/ygo_post.py 6
  instagram_gaming --run ygo_gods_dl` / `--run ygo_gods` (makes the `.light.mp4` copy itself); their YouTube holds stay
  until the strike week ends (`ygo_post.py 6 youtube --run <tag>`); also on the gaming Facebook Page on the owner's request (`store.FB_GAMING_IDS`). Next ideas the owner liked: Duel Links / Master Duel "best deck" videos from the live
  top-deck APIs (duellinksmeta.com / masterduelmeta.com `/api/v1/top-decks?sort=-created`), Duel Links card explainers
  (8,270 cards - start with the ~300 used in top decks), and a deck plan from the owner's own Duel Links collection
  (owner will share a phone screen recording of the card list, Skills and gems).
  Batch 3 (owner 2026-11-19: "20 - 5 Trap cards, 5 Spell Cards, 5 Monster Cards, 5 Best Deck"): orders 63-82 by
  `.cache/yugioh_batch3_write.py` - 15 card explainers (cards not in 1-62) + 5 Duel Links deck guides (Memento,
  Centur-Ion, Dark Magician, Icejade, Stardust / Synchron; newest list of each from the top-deck API, Skill text from
  `/api/v1/skills?name=`; the deck-type filter in the top-decks query does not work - pick ids from the newest 60).
  Built in `run_ygo3` (`laptop_batch.py ygo3 --extra-slot yugioh:20 --skip-retry`), waiting for the owner's review.
  Voice / card sync (owner 2026-11-20: "the voice is telling about the card but at that time previous card is
  displayed"): cinema scenes used to get equal slices of the video. Now a scene's optional `"cue"` (the script words
  where it starts) makes it cut in on that spoken word (`cinema_video.scene_starts`, edge-tts word timings; scenes
  without a found cue are spread between the found ones; no cues = old equal split). `yugioh_batch3_write.py` writes
  the cues and orders explainer scenes the way the narration says them. Queued videos are re-rendered in place with
  `.cache/cinema_rebuild.py <run folder> [n,...]`. Specs 1-62 got their cues from `.cache/yugioh_cues.py` (card name /
  section question / "Number N" found in the script, `OVERRIDES` for the odd ones) and were re-rendered in run_ygo,
  run_ygo_gods, run_ygo_gods_dl before their held YouTube uploads (their Instagram posts were already out of sync).
- Payana Museum shorts (owner's own phone videos of 14 Nov, `F:/AI AGENT/VID20261114*.mp4`): `.cache/carmuseum_specs.py` (25
  shorts, English text, original sound, facts from the museum boards / Wikipedia), `.cache/carmuseum_build.py`, approved by
  the owner 2026-11-16 (`carmuseum_out/approved.txt`), posted by `.cache/carmuseum_post.py <n>` to Instagram + gaming Facebook
  (2 a day there, `custom:carmuseum-` in `store.FB_GAMING_DAILY`) while YouTube is frozen by the strike.
- `daily_job.py` picks the lowest-`order` unposted, unexpired spec per slot (`--from-end` takes the
  highest, for manual batches that must not collide with scheduled runs).
  A failed build is not recorded, so the same spec comes back every run: a slot now tries up to 2 more specs (long
  videos: 1 more). Missing Wikipedia infobox images are the usual cause - no CineAI Hindi short went out 13-17 Nov
  2026 because Golmaal 5 and Jithu Madhavan had none (fixed with other `wiki:` refs).

### Main scripts
- `scripts/daily_job.py` - the one entry point: retries pending uploads, then builds + posts per
  slot (`--new-games`, `--classic-games`, `--new-palworld`, `--extra-slot slot:N`, `--long-videos`,
  `--long-category`, `--long-series`, `--deals-shorts`, `--deals-long`, `--skip-trends`, `--from-end`, `--skip-retry`). Exits 1 if every build or every
  upload failed (health check). Use `--skip-retry` for one-slot laptop batches: the retry step rebuilds old pending
  shorts, and on 2026-11-17 it rebuilt a Palworld breeding short with Wan motion, using the ZeroGPU quota.
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
  never shown/asked first** (unlike fusions). 2026-11-25 (owner chose "AI Trend Poster Instagram + AI Trend Poster FB Page"): rashifal is the one thing that
  still goes to the paused main Page, AND to its Instagram; built on the laptop for review (owner's Hindi voice,
  `MEDIA_OUTPUT_DIR=F:/AI AGENT/run_rashifal`, no `--post`), then `rashifal.py --date <day> --post-approved` posts the
  watched `made_all.json` video (refresh the run folder's store from gaming-state first, merge_state.py after).
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
- `scripts/laptop_batch.py <tag> <daily_job options>` - laptop batch of spec shorts (owner asks "10 Aniimo + 10 Palworld"): fresh run
  folder `F:/AI AGENT/run_<tag>` from the latest `gaming-state`, `daily_job.py --skip-trends --from-end` with every default slot 0,
  then `merge_state.py` + push (5 tries; a failed merge leaves `.unmerged` and the next run merges it first).
  `laptop_batch.py evo --evolution --max-shorts 3` runs `pokemon_daily.py` the same way (takes over `motion-cache` clips, keeps
  `run_evo` so a half-made line continues). **ZeroGPU quota**: the PRO quota resets at ~19:57 IST; `daily-pokemon.yml` usually
  starts ~19:30 IST, when the day's quota is already gone (12 Nov log: "105s requested vs. 50s left, try again in 0:27") - so the
  evolution shorts are made by the laptop task at 20:15 and the CI run is only the backup (it skips posted lines). Keep every
  other laptop ZeroGPU use (Qwen Space fusion art) for after that task.
  **Every evolution short is shown to the owner before upload** (owner 2026-11-14: "kabhi kabhi pokemon evolution bohot
  bakwas hote he, mujhe puch ke hi karna"): `pokemon_daily.py --review` builds without posting and lists the line in
  `run_evo/pokemon/review_pending.json` (held lines are not rebuilt); after the owner names the good ones:
  `laptop_batch.py evo --evolution --max-shorts 0 --post-approved <id,id>` (posts the exact file shown) and
  `--reject <id>` for the rest (`review_rejected.json`, never posted).
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
| 11:15 | `daily-pokemon.yml` - only retries unfinished uploads (`--max-shorts 0` since 2026-11-14: new evolutions are reviewed on the laptop first) |
| on push + 12:15 | `fusion-inbox.yml` - fusion clips uploaded to `inbox/` (daily run = retries) |
| 13:45 | `daily-anipal.yml` 1st half - 5 Aniimo + 2 Palworld guide + 3 breeding + 1 long (palworld) |
| 14:00 | `daily-palguide.yml` - 1 Palworld guide |
| 16:00 | `daily-deals.yml` - up to 10 free-game / deal shorts + 2 long deal roundups (Epic + Steam, live; no adult games) |
| 18:00 | `daily-long.yml` - 2 long gaming + 1 long movie |
| 19:00 | `daily-gameplay.yml` - 1 long + 8 shorts of own-stream clips from `clips/*` branches (laptop renders + pushes) |
| 20:15 | Laptop task **Pokemon Evolution Daily** (`scripts/evolution_job.cmd` -> `laptop_batch.py evo --evolution --max-shorts 3 --review`) - builds up to 3 evolution shorts right after the ZeroGPU quota reset, posts NOTHING, opens them in VLC (`evolution_review.py`); log `backend/logs/evolution_laptop.log` |
| 20:00 | `daily-rashifal.yml` - next day's 12 Hindi rashifal shorts, scheduled 06:00-06:55 IST on the AI Trend Poster FB Page |
| 21:00 | `daily-anipal.yml` 2nd half - 5 Aniimo + 3 Palworld guide + 2 breeding + 1 long (aniimo) |
| 18:00 | Laptop task **Car Museum Daily (approved shorts)** - `.cache/carmuseum_post.py 3`: 3 of the 25 owner-approved Payana Museum shorts a day to Instagram + gaming Facebook (light ~5 Mbps copies in `carmuseum_out/light`; 12 Mbps originals timed out in Instagram processing); does nothing once all are posted |
| 19:30 | Laptop task **GTA 6 FB Daily (approved shorts)** - `.cache/gta6_fb_daily.cmd`: next 6 of the 41 GTA 6 launch shorts to the gaming Facebook Page (`gta6_post.py 1-41 facebook_gaming`, cap reached = waits for the next day, no skip marker), then `laptop_batch.py gta6 --merge-only`; log `backend/logs/gta6_fb.log` |
| every 2 hours (since 2026-11-23; was hourly) | Laptop task **GTA 6 Instagram Hourly** - owner 2026-11-23: "har 2 ghante me 10 shorts ... youtube and instagram dono me"; `gta6_hourly.cmd` passes `--youtube 10`: up to 10 shorts that already have the GTA VI logo (`relogo_done.txt`) go to YouTube per run, but the Google project's ~10,000 units a day (1,600 an upload) stop it after ~5-6 a day - `run_gta6/youtube_quota_hit.txt` holds the Pacific date the quota ran out (no more tries that day). The rest as before: - `.cache/gta6_hourly.cmd` -> `.cache/gta6_hourly.py`: next 10 GTA 6 shorts (day2 21-65, day3a, day3b incl. short 11 since the owner allowed NPC-killing clips 2026-11-21, day4) + 1 long video of at most 15 min (Instagram's Reel limit; 720p30 ~2.2 Mbps `.ig.mp4` copy) to Instagram, owner 2026-11-21 ("har ghante me 10 shorts and 1 long video", "15 min tak wale instagram pe"). Stops at 95 of Instagram's 100 API posts per 24 h and goes on next hour; NudeNet hits go to `run_gta6/hourly_held.json` (ids the owner looked at and allowed go in `hourly_cleared.json` - shirtless Jason false alarms, 2026-11-22); `hourly.lock` makes the Facebook delete scripts wait (same Graph app quota). YouTube (all shorts + the longer longs) after the strike week - its quota allows only ~6 uploads a day; log `backend/logs/gta6_hourly.log` |
| (DISABLED 2026-11-25 - `gta6-youtube.yml` does it, laptop off) | Laptop task **GTA 6 YouTube 4h** (owner 2026-11-25: "youtube gaming ke liye 4 ghante me 10 shorts") - `.cache/gta6_youtube4h.cmd` -> `gta6_hourly.py --shorts 0 --longs 0 --youtube 10 --wait-lock` (waits up to 90 min for a running Instagram run's `hourly.lock`): 10 GTA 6 shorts to YouTube; the 2-hourly Instagram task now passes `--youtube 0`. Same task settings (WakeToRun, StartWhenAvailable, battery). YouTube's channel limit is ~14-15 uploads in a rolling 24 h, counting the owner's own manual uploads (24 Nov: 15 in 24 h, 3 of them the owner's), so a run gets what the window has left. |
| always running (chain of ~5.5 h runs since 2026-11-25) | `gta6-youtube.yml` **now**: GitHub started only 2 scheduled runs a day (hourly cron; the 07:30 login check ran 9 h late), so each run keeps going ~5.5 h, tries every 30 min (max 10 GTA 6 shorts in any 4 h, `gta6_youtube_cloud.PER_4H`), re-reads + saves gaming-state each round, then starts the next run itself (`gh workflow run` with the GITHUB_TOKEN, `actions: write`); cron every 3 h and a push to the workflow file restart a broken chain; own concurrency group `gta6-youtube`. OLD text: `gta6-youtube.yml` (owner 2026-11-23: "office me laptop open nahi kar sakta ... har 2 ghante me youtube me shorts jaye GTA 6 ke") - uploads the next GTA 6 short from the branch `clips/gta6-youtube` (`queue.json` + `shorts/*.mp4`, downloaded one file at a time from raw.githubusercontent.com - the repo is public) to YouTube; ~5-6 a day get through the API quota, a quota error ends the run. The laptop fills the queue with `scripts/gta6_youtube_cloud.py push N` (logo shorts, NudeNet-clean, not on YouTube, in the hourly posting order; 40 pushed 2026-11-23 = about a week). GitHub had not started a single scheduled run of it by 24 Nov 00:10 IST (schedules here run hours late), so the laptop task posts YouTube again (`--youtube 10`) and skips every short on the GitHub queue (`wt_gta6yt/queue.json`), so nothing goes up twice. YouTube goes in story order (`YT_ORDER`: launch 1-41, day2 1-20, then the hourly order); the 184 old shorts posted to Instagram before the logo get it via `gta6_relogo.py --all` (40 a run, added to `gta6_hourly.cmd`, owner 2026-11-24: "purane wale shorts bhi dalna padenga"). The `.env` `GITHUB_TOKEN` is expired (401) - a manual "Run workflow" needs the website. Laptop task settings (owner 2026-11-24: "agar wo sleep me chala jaye tab bhi chalu rahna chahiye"; nothing ran after 23:00 on 23 Nov): WakeToRun + StartWhenAvailable on, runs on battery (DisallowStartIfOnBatteries / StopIfGoingOnBatteries off), wake timers on (powercfg RTCWAKE AC/DC 1; Modern Standby has no network while asleep, battery idle sleep after 4 min), and `gta6_hourly.cmd` waits up to ~3 min for the internet before posting. First GTA 6 shorts on YouTube 24 Nov 10:06-10:27 IST (launch 1-5, day4 32-37 minus held 33); the 11th got `uploadLimitExceeded` - the CHANNEL's own daily upload limit (rolling 24 h, ~10 a day for this channel), hit before the API quota. Still refused at 22:21 the same day. `gta6_hourly.py` now just stops YouTube for that run (`run_gta6/youtube_limit_until.txt`, epoch, 90 min) and tries again on the next 2-hour run, so the first run after the limit opens uploads (a 22 h pause had skipped every afternoon run). When Instagram's 95-a-day cap is reached the run still goes on to YouTube (before 24 Nov 21:00 it returned early and skipped it). So YouTube gets ~10 GTA 6 shorts a DAY, not 10 every 2 hours. `tools/keep_awake.py` (SetThreadExecutionState, owner 2026-11-24: "screen ko idle mat jane dena") keeps the laptop and screen awake while it runs. |
| 19:00 | Laptop task **Yu-Gi-Oh Daily (approved explainers)** - `.cache/ygo_post.py 3`: next 3 of the 50 Yu-Gi-Oh videos in `run_ygo/review_queue.json` to Instagram (`.light.mp4` ~5 Mbps copies - cinema-style originals are 100+ MB and time out); YouTube holds stay until the strike week ends (then `ygo_post.py N youtube`) |
| Sun 19:30 | `weekly-report.yml` - stats issue |

All gaming workflows share the `daily-gaming` concurrency group (never two writers of `gaming-state`).

## Decisions and owner rules (do not break)

- **NOTHING IS POSTED WITHOUT THE OWNER'S APPROVAL** (owner 2026-11-16). The YouTube channel was TERMINATED over an
  adult game in a deal video (appealed back), then got a community-guideline strike (no YouTube uploads for a week from
  ~2026-11-16) - over: the first upload after it (Deino evolution, `ZTwLTNT76YU`) went through on 2026-11-23 (last one before it: 15 Nov 17:52 IST) for the "GRAND TIDDY ADVENTURES" deal short of 14 Nov: its Steam screenshot showed sexual content. It was
  posted at 22:29 IST by a deals run that started before the adult filter was pushed (22:45). Since then:
  every workflow's `schedule` is removed (manual `workflow_dispatch` only, run only after the owner approved the videos),
  the laptop task "AI Trend Poster Daily" is disabled, and every laptop batch runs with `POST_MODE=review`:
  `daily_job.Poster.post` / `post_long` only queue the finished video in `<run folder>/review_queue.json` and put a
  `hold:review` marker on each platform (so no retry, CI included, posts it). `scripts/post_reviewed.py <run> show`
  opens them in VLC; `approve 1,3` posts exactly those files, `reject 2` marks `skip:rejected`; then
  `laptop_batch.py <tag> --merge-only`. Shorts send Instagram a ~3 Mbps `<video>.light.mp4` copy made at approve time (Instagram pulls the
  file through the laptop tunnel; 45 MB reels timed out in its processing on 2026-11-17); other platforms get the original. Evolutions keep their own review (`--review`). Rashifal also needs approval now.
- **Automatic nudity check before every post / review** (owner 2026-11-16: "jo bhi videos aap banaoge kisi games clips
  se to waha check kar lena ki koi nudity to nahi he"): `app/media/safety.scan_video` runs NudeNet (local onnx) on one
  frame a second; any exposed breast / genitalia / buttocks / anus hit holds the video for the owner (flagged frames in
  `<video>.nsfw/`, result cached in `<video>.safety.json`). It fails CLOSED (no NudeNet = unsafe). Called by
  `daily_job.Poster.post`, `post_long` and `gameplay_clips.upload`; `post_reviewed.py approve` skips it because the owner
  watched the video. Shirtless wrestlers/fighters are sometimes flagged as "breast" - a human look decides.
  2026-11-16 channel scan (`.cache/channel_scan.py`): all 3,912 thumbnails clean (10 false alarms: WWE, Mortal Kombat).
- **Community guidelines (YouTube / Instagram / Facebook)**: zero adult or sexual content anywhere - no nudity, no
  suggestive images, no adult games (any Steam content descriptor 1/3/4), no sexual-violence themes. Look at EVERY
  picture/clip that goes into a video before showing it to the owner. Also no gore close-ups, hate symbols, slurs,
  real-person insults, misleading claims or copyrighted film/TV clips.
- **Routing**: gaming never goes to the movie channel/page and vice versa. Gaming = English, movies = Hindi.
- **No adult games in deal videos** (owner 2026-11-14, after a platform warning for "Kiss Me on the Oligarch's Dime"
  in a 25 Oct roundup and the "Evenicle" short: "adult wale games mat dalna"): `store_deals._adult` skips Steam
  content descriptors 1/3/4 (any nudity / sexual content - this also drops Cyberpunk, GTA V, Skyrim) and adult words
  in the name / description / genres. Old deal uploads are not retried (`daily_job.retry_pending` skips them - offers expire).
- **Store deals** (when running): the store APIs are the source (no two-source check), prices are Steam India INR, and
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
- **Gaming Facebook Page PAUSED + wiped** (owner 2026-11-20: "sala facebook me kuch bhi views nahi aa rahe"): views per
  video went from ~180 avg (Jun-Aug 2026, the owner's own clips: GTA 6 PC joke 1,023, Shiny Articuno 865) to 0.2 once
  the pipeline posted ~1,070 videos (Oct-Nov), and even the owner's own GTA 6 launch clips got 0-3 - Page-wide reach
  loss, not a publishing error (all videos were published, ready, in the Reels list). Owner chose to delete EVERY video
  on the Page and start fresh. Backup first: `F:/AI AGENT/fb_gaming_backup/` (`videos_list.json` = all 1,173 with views
  and links, `videos/` = the owner's 103 pre-21-Oct clips as Facebook serves them, `delete_all.py` + `deleted.txt`).
  `store.FB_GAMING_PAUSED = True`: nothing goes to `facebook_gaming` automatically (uploads get `skip:fb-paused`,
  capped prefixes have 0 left). Restart plan proposed: 1 Reel a day, the owner's own best clip with a hook title,
  posted from the Facebook app (trending audio), 19:00-22:00 IST.
  Main Page "AI Trend Poster" the same day ("Delete kro"): 0 followers, 341 pipeline videos, 38 views in total since
  Sep 2026 - all deleted (`F:/AI AGENT/fb_main_backup/`: list + `delete_all.py`), `store.FB_MAIN_PAUSED = True`
  (`store.fb_main_allowed` in `daily_job.Poster`, `rashifal.py` posts nothing). Restart idea: rename it after the Hindi
  YouTube channel, first ~100 followers by the owner's own invites / shares, then 1 Hindi movie Reel a day.
- **Gaming Facebook Page volume** (owner 2026-11-14): only Pokemon evolutions, fusions (Pokemon / Dragon Ball / Mewtwo
  stones / compilations) and at most 2 own-stream clips a day (IST) go to `facebook_gaming` (GTA 6 launch shorts: 6 a day,
  owner 2026-11-19 - `"gameplay:gta6_"` (all GTA 6 plans together) comes first in `store.FB_GAMING_DAILY`; `merge_state.py` lets a real post
  replace an old `skip:fb-volume` marker); every other gaming upload
  (Aniimo, Palworld, deals, classic, new, GTA 6, long videos) gets a `skip:fb-volume` marker there and stays on YouTube +
  Instagram. Single exceptions the owner asks for go in `store.FB_GAMING_IDS` (exact ids): the 12 Yu-Gi-Oh Egyptian God
  videos (2026-11-17, "Facebook gaming me bhi dal do"). Gate: `store.fb_gaming_allowed` (called by `daily_job.Poster.post`, `post_long`, `gameplay_clips.upload`).
  Why: Jun-Aug 2026 the owner's own clips got 100-340 views each (Shiny Articuno 864, GTA 6 joke 1,023); reach already
  fell to ~13 in Sep; since 21 Oct ~1,000 pipeline videos at 50-90 a day got 0.2 views each. Page status: no
  restrictions, Recommendations active - it is distribution, not a penalty. Compare views again early Dec.
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
- 2026-11-25 - Rashifal back on the AI Trend Poster FB Page + Instagram after review (`rashifal.py --post-approved`); own-voice review batches (`.cache/review_chain.sh`, `.cache/revoice_evo.py` re-voices built evolution shorts from cached clips).
- 2026-11-25 - `gta6-youtube.yml` runs as a self-restarting chain (~5.5 h runs, a try every 30 min) because GitHub started only 2 scheduled runs a day; 30 GTA 6 shorts went to YouTube 24 Nov 00:55 - 25 Nov 18:56.
- 2026-11-25 - Owner verified the YouTube channel (phone / advanced features): 10 uploads went through at 00:55 while the old ~12-15 a day limit was full. Laptop "GTA 6 YouTube 4h" task disabled; GitHub alone posts 10 in 4 hours.
- 2026-11-25 - `gta6-youtube.yml` now tries hourly and uploads up to 10 GTA 6 shorts in any 4 hours (counts laptop uploads too; stops on the channel upload limit); queue in story order (launch 6-41, day 2 first), 96 shorts (owner: "bina laptop on kiye karo").
- 2026-11-25 - GTA 6 YouTube moved to its own laptop task: 10 shorts every 4 hours from 04:30 (`gta6_youtube4h.cmd`); the 2-hourly task is Instagram only.
- 2026-11-24 - Pokemon evolution / fusion / stone shorts also narrated in the owner's voice (`lucario_stone_video._tts`).
- 2026-11-24 - GTA 6 laptop task: YouTube is tried on every 2-hour run after the channel upload limit (no 22 h pause) and also when Instagram's daily cap is full.
- 2026-11-24 - First 10 GTA 6 shorts on YouTube; the channel's daily upload limit (`uploadLimitExceeded`, ~10 a day) now pauses YouTube for ~22 h.
- 2026-11-24 - GTA 6 laptop task wakes the laptop from sleep, runs on battery and catches up missed runs; waits for the internet first.
- 2026-11-24 - Narration in the owner's own voice on the laptop (`OWNER_VOICE`, `scripts/owner_voice.py`): English = Indian English edge-tts + Chatterbox VC, Hindi = Chatterbox multilingual TTS; laptop GTA 6 task posts YouTube again next to the GitHub queue.
- 2026-11-23 - `gta6-youtube.yml` + `scripts/gta6_youtube_cloud.py`: GTA 6 shorts go to YouTube every 2 hours from GitHub Actions (queue on `clips/gta6-youtube`), so the laptop can stay closed.
- 2026-11-23 - GTA 6 shorts get our own "GTA VI" sunset logo (`gameplay_clips.gta6_logo`, re-render `.cache/gta6_relogo.py`); day 6 + 7 in the hourly Instagram queue; YouTube strike over (first upload 23 Nov).
- 2026-11-22 - `pokemon_daily.py` no longer deletes a line's folder while it waits in review_pending.json or review_queue.json (a `hold:review` marker counted as posted: Deino's final.mp4 and the 11 strike-held YouTube evolution shorts were deleted; the 11 were re-downloaded at 720p from their Instagram posts via the Graph `media_url`, Deino rebuilt).
- 2026-11-22 - Evolution shorts: Hydreigon animated locally (Wan gave it hands), fan Gigantamax v3 with 3 heads, add-only fan Mega (`MEGA_DESIGN`), `skip_forms`; Deino rebuilt.
- 2026-11-21 - Evolution shorts: own fan Gigantamax design per Pokemon (`GMAX_DESIGN`, v3 art) and Wan poses (`POSE`); Axew, Bagon, Trapinch rebuilt.
- 2026-11-21 - GTA 6 day-5 recording: plan `gta6_day5` (89 shorts + 7 long), rendered and checked, waiting for the owner.
- 2026-11-21 - GTA 6 hourly Instagram posting (10 shorts + 1 long up to 15 min an hour, laptop task `gta6_hourly.py`) after the owner's go-ahead for all GTA 6 clips.
- 2026-11-21 - GTA 6 day-4 recording: plan `gta6_day4` (102 shorts + 10 long); first edited YouTube video - "GTA 6 Chapter 1" (31:46, hook + chapter cards + text pop-ups + zooms, English SRT from OCR of the game subtitles).
- 2026-11-20 - Evolution shorts: Gigantamax clearly different from Dynamax (changed body, type colour), fan Mega as an edit, free ZeroGPU fallback, no short without its forms; GTA 6 day-3b plan grown to 102 shorts.
- 2026-11-20 - GTA 6 day-3 recordings: plans `gta6_day3a` (3 shorts) + `gta6_day3b` (72 shorts + 8 long).
- 2026-11-20 - Both Facebook Pages wiped on the owner's request (lists in `fb_gaming_backup/`, `fb_main_backup/`; the owner's 103 own gaming clips downloaded first); automatic posting to both paused (`store.FB_GAMING_PAUSED`, `FB_MAIN_PAUSED`).
- 2026-11-20 - Cinema videos: scenes cut on the narration word of their `cue` (Yu-Gi-Oh cards were shown one scene late); batch 3 re-rendered.
- 2026-11-19 - Yu-Gi-Oh batch 3 (orders 63-82: 15 card explainers + 5 Duel Links deck guides).
- 2026-11-19 - GTA 6 day-2 recording: plan `gta6_day2` (65 shorts + 5 long); `gta6_post.py --plan`; FB cap prefix `gameplay:gta6_` covers all GTA 6 plans; faster-whisper on the GPU (cuBLAS/cuDNN wheels in the venv).
- 2026-11-19 - Gaming Facebook Page: GTA 6 launch shorts 6 a day (were capped at 2, shorts 3-9 had got permanent skip markers); laptop task "GTA 6 FB Daily" 19:30; `store.fb_gaming_left_today`.
- 2026-11-19 - GTA 6 launch stream: 41 shorts + 2 long videos (`gta6_launch` plan), strict 2 fps nudity pass after NudeNet missed a beach shot; posting 10 at a time (`.cache/gta6_post.py`).
- 2026-11-17 - `laptop_batch.py evo` no longer stops on the strike-time YouTube holds in `run_evo/review_queue.json` (the 20:15 evolution task built nothing on 17 Nov). ElevenLabs Image & Video API needs a Pro plan (Starter: 402); HF credits ran out the same evening.
- 2026-11-17 - Evolution shorts: Mega + Dynamax + Gigantamax for every line, fan-made art when the games have none (`with_forms`, `fan_form`, `fal_video.dynamax_clip`).
- 2026-11-17 - `post_reviewed.py approve`: Instagram gets a light copy; 12 Yu-Gi-Oh God videos + 5 CineAI Hindi videos posted after the owner's approval.
- 2026-11-17 - daily_job: a failed spec build no longer blocks its slot (tries the next specs); 3 movie specs with missing Wikipedia images fixed.
- 2026-11-17 - Yu-Gi-Oh Egyptian God videos: deck + how-to-summon for Slifer, Obelisk and Ra (specs 51-56 Master Duel / TCG, 57-62 Duel Links); `daily_job.py --skip-retry`.
- 2026-11-17 - `PROJECT_OVERVIEW.md`: one-page map of the project for new sessions (status, flow, repo map, rules).
- 2026-11-16 - Yu-Gi-Oh explainer slot (`yugioh`, cinema style for gaming specs); Payana Museum shorts approved and posting to Instagram/Facebook.
- 2026-11-16 - NudeNet check (`app/media/safety.py`) on every video before posting or review; flagged videos are held for the owner.
- 2026-11-16 - All scheduled uploads stopped after a YouTube strike (adult deal short); review queue for every laptop batch (`POST_MODE=review`, `post_reviewed.py`, `hold:review` markers).
- 2026-11-14 - Deal videos: no adult games (`store_deals._adult`: Steam descriptors 1/3/4 + adult words) after a platform warning; old deal uploads are not retried.
- 2026-11-14 - Evolution shorts need the owner's OK: the laptop task builds + opens them in VLC (`--review`, `evolution_review.py`), posts only `--post-approved` lines; CI builds no new ones.
- 2026-11-14 - Gaming Facebook Page gets only evolutions, fusions and 2 own-stream clips a day (`store.fb_gaming_allowed`): ~1,000 pipeline videos there averaged 0.2 views.
- 2026-11-12 - Evolution shorts move to a laptop task at 20:15 IST (`evolution_job.cmd`, `laptop_batch.py`): the CI run comes just before the ZeroGPU quota reset and found it empty; Magikarp, Froakie, Gible posted from the laptop.
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
