# Vocabulary Database V2

This package updates the supplied Vocabulary Bot database before any further bot changes.

## What was changed
- Preserved all 3,521 production records.
- Added structured Bangla meaning items.
- Added `meaning_bn_photo` using ` • ` separators.
- Added `meaning_bn_post` using comma-separated meanings with a final Bengali danda.
- Preserved original values in `source_fields`.
- Applied only high-confidence meaning/pronunciation corrections found directly in the supplied data.
- Added verification metadata and pronunciation variants for known heteronym cases.

## Important
This is a database cleanup pass, not a claim of independent lexical verification of every Bengali translation. Records that need deeper lexical review are explicitly flagged.

The original source database is not overwritten.
