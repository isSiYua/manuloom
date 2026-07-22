# Contributing to ManuLoom

Thank you for helping make detailed, source-grounded notes easier to produce.

## Good first contributions

- Reproducible fixes for public source adapters.
- Documentation, translations, installation checks, and copyright-safe examples.
- Browser-extension accessibility and packaging improvements that preserve narrow permissions.
- Tests based on synthetic fixtures or user-owned evidence.

Do not attach private notes, model keys, Cookies, downloaded creator media, or proprietary transcripts to an issue.

## Development setup

```bash
python3 -m pip install -r scripts/requirements.txt
PYTHONPATH=scripts python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v
```

The standard suite uses mocks and fixtures and must not call paid models or download source media. Run network/manual checks separately and disclose them in the pull request.

## Architecture boundary

Source adapters acquire ordered evidence. They must feed the shared manuscript pipeline rather than introducing a platform-specific writer. Python owns deterministic orchestration and validation; the text model owns semantic organization and wording.

Changes to the editorial prompts, paragraph text, chapter planning, coverage gates, ASR reconciliation, or golden style require a focused proposal and before/after evaluation artifacts. A source-adapter pull request should not modify them.

## Pull requests

1. Keep one concern per pull request.
2. Add tests that fail before the change and pass after it.
3. Run the full offline suite.
4. Update documentation and third-party notices when behavior or provenance changes.
5. State whether dependencies, paid model calls, permissions, or public network behavior changed.

By contributing, you agree that your contribution is licensed under the repository's MIT License and that you have the right to submit it.
