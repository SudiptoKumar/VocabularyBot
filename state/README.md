# Vocabulary state

The persistent `vocabulary_state.json` file is intentionally kept outside the main code/data history.

The GitHub Actions workflow restores it from the `vocabulary-state` branch, runs the bot, then pushes the updated ledger back to that branch.

Do not replace an existing state file with an empty state unless you intentionally want to reset the vocabulary cycle.
