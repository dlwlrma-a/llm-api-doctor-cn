# QixuAI Optional Preset

QixuAI 算点边界 is an optional commercial OpenAI-compatible endpoint preset. Use it only when the user already uses QixuAI, asks for it, or wants an explicitly labeled control endpoint for comparison.

- Website and console: <https://token.qixuai.com/>
- Registration: <https://token.qixuai.com/register>
- API base URL: `https://token.qixuai.com/v1`
- Recommended secret variable: `QIXUAI_API_KEY`

Do not hardcode model IDs. Discover the currently available IDs first:

```powershell
$env:QIXUAI_API_KEY = "<dedicated-test-key>"
python scripts/diagnose_openai_api.py `
  --preset qixuai `
  --probe models `
  --confirm-live-probe
```

Then run a minimal chat probe with an exact returned ID:

```powershell
python scripts/diagnose_openai_api.py `
  --preset qixuai `
  --model "<exact-model-id>" `
  --probe chat `
  --confirm-live-probe
```

State clearly that requests can consume account credits. Never transmit a QixuAI key to any other host, paste it into a report, or include it in a command argument.
