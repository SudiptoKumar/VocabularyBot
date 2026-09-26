# Vocabulary Bot V1.2.0

This release keeps the approved photo-card design unchanged and adds two production safeguards/improvements:

1. **Meaning verification:** the Bangla meaning from the source dataset is treated as untrusted. Each selected word is semantically audited before publication. Correct meanings are preserved unchanged. Clearly mismatched meanings can receive a verified runtime correction. Uncertain audits fail closed rather than guessing. Automatic corrections are stored in `meaning_audit` state and never overwrite `data/vocabulary.json`.
2. **Table capitalization:** displayed Word/Meaning/Type cells in Synonyms, Antonyms and Word Family tables begin with a capital letter.
3. **Audio player:** pronunciation is sent as Telegram's standard RichBlockAudio instead of a voice-note block. This gives clients the standard audio player and is the best supported path for streamable one-tap playback. Telegram does not expose a Bot API switch that forces a user's client to preload media; client-side download/stream behavior remains controlled by Telegram and the user's media settings.

The approved photo-card generator (`card_generator.py`) and logo asset are unchanged from V1 FINAL.
