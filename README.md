# Vocabulary Bot V1

Production-oriented Telegram vocabulary publisher using:

- 3,521 unique normalized vocabulary words from the supplied database
- 5 random words per run
- no-repeat shuffle-bag rotation
- persistent state on a dedicated Git branch
- atomic state writes
- stale reservation recovery
- AI enrichment through Cerebras (optional, recommended for full rich content)
- compact 8:5 editorial cards (1080×675)
- Playfair Display for the hero word
- Plus Jakarta Sans for English/UI
- Hind Siliguri for Bangla
- supplied red V logo
- Telegram Rich Messages with photo/audio blocks, real tables, headings, dividers and expandable details
- native Telegram quiz polls (current `correct_option_ids` API format)
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
3. Give it permission to post messages and polls.
4. The supplied channel ID is configured by default as `-1004330016419`, but `TELEGRAM_CHANNEL_ID` should be set as a GitHub secret or repository variable.

## GitHub secrets

Required:

- `TELEGRAM_BOT_TOKEN`

Recommended for full enrichment:

- `CEREBRAS_API_KEY`

Optional variables:

- `TELEGRAM_CHANNEL_ID` (default: `-1004330016419`)
- `CEREBRAS_MODEL` (default: `gpt-oss-120b`)
- `CHANNEL_NAME` (default: `Vocabulary`)
- `TIMEZONE` (default: `Asia/Dhaka`)
- `WORDS_PER_RUN` (default: `5`)
- `AUDIO_ENABLED` (default: `true`)
- `QUIZ_ENABLED` (default: `true`)
- `QUIZ_PER_WORD` (default: `true`)

## Scheduling

The included workflow is scheduled once per day at 08:00 Asia/Dhaka (02:00 UTC). It can also be triggered manually with `workflow_dispatch` and a custom word count.

## State model

The bot does not use `random.sample()` independently on every run. It keeps a shuffled queue and consumes it in batches of five. When the queue becomes shorter than five, the remaining words finish their current cycle and the next words are drawn from a newly shuffled cycle. This preserves the 5-word run size while ensuring every word is used before the pool fully repeats.

The state ledger also stores publication history, generated AI content, Telegram media file IDs, recent runs and recovery information.

## AI separation

The master vocabulary database remains the source of truth for the supplied word, pronunciation, IPA and Bangla meaning. AI generates derived teaching material such as English definitions, examples, synonyms, antonyms, word family, collocations, common mistakes, memory hooks and quiz options.

If AI is unavailable, the bot falls back to safe source-only content and still attempts publication.

## Card design

Card ratio: 8:5, 1080×675.

The card intentionally contains only the visual memory anchor:

- logo
- Daily Vocabulary label
- word
- IPA
- part of speech / CEFR when available
- Bangla meaning
- short English meaning when available
- simple LISTEN cue (non-interactive)

The Rich Message contains the detailed learning material so the image does not duplicate the whole post.

Font files are not bundled in this repository. The workflow downloads the selected Google Fonts at runtime into `.runtime/fonts`, which is gitignored.

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


## V1.0.3 reliability update
- Compact 1080×675 vocabulary card.
- Cleaner rich post headings and bold table/section labels.
- Type remains on the card only.
- Pronunciation audio is delivered as a Telegram voice note without a caption.
- Media cache is versioned so the new presentation is applied to previously cached words.
