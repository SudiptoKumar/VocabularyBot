# Vocabulary Bot V1.2.5

This release keeps the approved photo-card design unchanged and hardens semantic meaning protection.

## Meaning protection

The Bangla meaning from `data/vocabulary.json` is treated as untrusted source data. For selected words the bot performs a two-stage semantic audit:

1. **Clearly correct** → keep the source meaning unchanged.
2. **Clearly wrong** → correct it only with a high-confidence semantic decision.
3. **Uncertain/unavailable** → retain the original source meaning and continue the run; the bot does not invent a correction and does not abort all five words.

A second per-word judge is used for borderline cases. The audit is versioned so old uncertain decisions are re-evaluated after deployment. The original database is never overwritten.

Critically, a confirmed correction is propagated through the actual `WordEntry` used by both card and rich-message publication, preventing a stale database meaning from being reintroduced at the final publishing step.

The known source-data mismatch for `tall` is covered by regression tests.

## Audio behavior

Pronunciation is sent as Telegram native voice-note media, restoring the earlier voice-note UX. Once the client has downloaded the voice message, Telegram can replay the cached audio repeatedly. The bot cannot force client-side preloading because that behavior is controlled by Telegram and the user's media settings.

Audio is post-processed when ffmpeg is available: leading/trailing silence is trimmed, and playback is shortened toward a dynamic target of about 1 second for short words and up to 2 seconds for long words. Very short pronunciations are not artificially stretched.

## Other retained V1.2 behavior

- Synonym, antonym and word-family table entries start with a capital letter.
- Pronunciation uses Telegram's native RichBlockVoiceNote block for the earlier replay/download UX.
- Existing no-repeat shuffle/state safeguards remain intact.
- Existing approved photo-card generator and logo are unchanged.

## Deployment

Keep the existing `vocabulary-state` branch. Do not replace its state file with the release ZIP.


## Database V2
Production data/vocabulary.json is Vocabulary Database V2 (schema 2.0), including verified/corrected pronunciation and structured photo/post Bangla meanings. The original source values are preserved per record in source_fields, with database_corrections.json and verification_report.json included for auditability.


## Production Database Integration

The production source is Vocabulary Database V2 (schema 2.0) with 3,521 records. The bot uses the verified/corrected database fields for pronunciation, IPA, photo Bangla meaning, and post Bangla meaning.

Database V2 currently has 3,503 production-eligible records. 18 records are intentionally held out of the live shuffle because they are flagged for deeper review or do not yet have a production Bangla meaning. They remain in the database and can be enabled after a later verification pass.

The production state branch is compatible because the database IDs and normalized terms remain unchanged.


## V1.2.5 updates
- Corrected `century` pronunciation display to `সেঞ্চুরি`.
- Restored the standard Telegram audio player with visible title `Vocabulary - <Word>`.
- Audio is generated at normal speaking speed and padded with silence when needed to hit a dynamic 1.0 / 1.5 / 2.0 second minimum bucket; speech is never time-compressed.


Audio playback: the bot uses Telegram RichBlockAudio/standard music audio, uploads a small MP3 on demand, reuses its Telegram file_id, and sets title to the word with performer "Vocabulary" so clients render "Vocabulary - <Word>" without duplication. The Bot API does not expose a control to force a client to reset the playhead to 0 after completion; replay-at-end behavior is therefore client-side.
