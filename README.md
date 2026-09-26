# Vocabulary Bot V1.2.1

This release keeps the approved photo-card design unchanged and hardens semantic meaning protection.

## Meaning protection

The Bangla meaning from `data/vocabulary.json` is treated as untrusted source data. For selected words the bot performs a two-stage semantic audit:

1. **Clearly correct** → keep the source meaning unchanged.
2. **Clearly wrong** → correct it only with a high-confidence semantic decision.
3. **Uncertain/unavailable** → retain the original source meaning and continue the run; the bot does not invent a correction and does not abort all five words.

A second per-word judge is used for borderline cases. The audit is versioned so old uncertain decisions are re-evaluated after deployment. The original database is never overwritten.

Critically, a confirmed correction is propagated through the actual `WordEntry` used by both card and rich-message publication, preventing a stale database meaning from being reintroduced at the final publishing step.

The known source-data mismatch for `tall` is covered by regression tests.

## Other retained V1.2 behavior

- Synonym, antonym and word-family table entries start with a capital letter.
- Pronunciation uses Telegram's standard RichBlockAudio player.
- Existing no-repeat shuffle/state safeguards remain intact.
- Existing approved photo-card generator and logo are unchanged.

## Deployment

Keep the existing `vocabulary-state` branch. Do not replace its state file with the release ZIP.
