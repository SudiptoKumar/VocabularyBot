# Vocabulary Bot V1

Production-oriented Telegram vocabulary publisher using:

- 3,521 unique normalized vocabulary words from the supplied database
- 5 random words per run
- no-repeat shuffle-bag rotation
- persistent state on a dedicated Git branch
- atomic state writes
- stale reservation quarantine (no automatic requeue of unknown Telegram send outcomes)
- AI enrichment through Cerebras (required for uncached words; publication fails closed if complete enrichment is unavailable)
- compact 8:5 editorial cards (1080×675)
- Playfair Display for the hero word
- Plus Jakarta Sans for English/UI
- Hind Siliguri for Bangla
- supplied red V logo
- Telegram Rich Messages with photo/audio blocks, real tables, bold headings, one divider and a native pullquote
- audio generation with edge-tts and an espeak/ffmpeg fallback
- per-word failure isolation

## Repository layout

```text
VocabularyBot/
├── main.py
├── config.py
├── dataset.py
├── state_store.py
├── shuffle_bag.py
├── content_ai.py
├── audio.py
├── font_manager.py
├── card_generator.py
├── rich_message.py
├── telegram_client.py
├── data/vocabulary.json
├── assets/vocabulary_logo.png
├── state/README.md
└── .github/workflows/vocabulary.yml
```

## Telegram setup

1. Create a bot with BotFather.
2. Add the bot as an administrator of the private `Vocabulary` channel.
3. Give it permission to post messages.
4. The supplied channel ID is configured by default as `-1004330016419`, but `TELEGRAM_CHANNEL_ID` should be set as a GitHub secret or repository variable.

## GitHub secrets

Required:

- `TELEGRAM_BOT_TOKEN`

Recommended for full enrichment:

- `CEREBRAS_API_KEY`

Optional variables:

- `TELEGRAM_CHANNEL_ID` (default: `-1004330016419`)
- `CEREBRAS_MODEL` (default: `gpt-oss-120b`)
- `CEREBRAS_REASONING_EFFORT` (default: `low`)
- `CHANNEL_NAME` (default: `Vocabulary`)
- `TIMEZONE` (default: `Asia/Dhaka`)
- `WORDS_PER_RUN` (default: `5`)
- `AUDIO_ENABLED` (default: `true`)

## Scheduling

The included workflow is scheduled once per day at 08:00 Asia/Dhaka (02:00 UTC). It can also be triggered manually with `workflow_dispatch` and a custom word count.

## State model

The bot does not use `random.sample()` independently on every run. It keeps a shuffled queue and consumes it in batches of five. When the queue becomes shorter than five, the remaining words finish their current cycle and the next words are drawn from a newly shuffled cycle. This preserves the 5-word run size while ensuring every word is used before the pool fully repeats.

The state ledger also stores publication history, generated AI content, Telegram media file IDs, recent runs and recovery information.

## AI separation

The master vocabulary database remains the source of truth for the supplied word, pronunciation, IPA and Bangla meaning. AI generates derived teaching material such as English definitions, examples, synonyms, antonyms, word family and memory hooks.

If AI enrichment is unavailable or incomplete, the run fails closed and the selected words are returned to the queue. The bot does not publish degraded source-only lessons.

## Card design

Card ratio: 8:5, 1080×675.

The card intentionally contains only the visual memory anchor:

- logo
- Daily Vocabulary label
- word
- part of speech / CEFR
- Bangla meaning
- short English meaning when available

Pronunciation/IPA and audio controls remain in the Telegram post instead of the image.

The Rich Message contains the detailed learning material so the image does not duplicate the whole post.

Font files are not bundled in this repository. The workflow downloads the selected Google Fonts at runtime into `.runtime/fonts`, which is gitignored.

## Publication safety

Card generation and pronunciation audio are required when their features are enabled. A local media generation failure blocks that word from publication rather than sending an incomplete lesson.

For Telegram sends, confirmed API rejections are recorded and the word is requeued for a later run. Rate limits are retried automatically. A transport or 5xx response after the request leaves the delivery outcome unknown, so the word is quarantined instead of being automatically requeued. This deliberately favors avoiding duplicate posts over automatically recovering an ambiguous send. The quarantined item must be resolved manually after checking the channel.

## Local tests

```bash
python -m py_compile *.py
python main.py --self-test
python main.py --preview --count 5
python main.py --dry-run --count 5
```

For a real publication run, configure the Telegram token and run:

```bash
python main.py --count 5
```


## V1.1.0
- Final rich post: large word heading, pronunciation, Meaning, `অর্থ⦂ meaning, meaning।`, audio, one divider before Example, centered table captions for Synonyms/Antonyms/Word Family, and a native Memory Hook pullquote.
- Common Collocations, Common Mistake and Quiz publication are removed.
- Photo card visual design is unchanged. Only card Bangla multiple-meaning separators are normalized to `•`.
- AI placeholder meaning/definition text is rejected by the quality gate.
