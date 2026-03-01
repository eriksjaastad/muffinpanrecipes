# OpenClaw Preflight Checklist

Use this checklist before running any pipeline or test that depends on secrets.

## Required Working Directory

```bash
cd "$HOME/projects/muffinpanrecipes"
pwd
```

Expected `pwd` output:

Expected output should end with: `projects/muffinpanrecipes`

## Mandatory Secret Check

Do not print the key value. Only check that it exists.

```bash
doppler run -- sh -lc 'if [ -n "$STABILITY_API_KEY" ]; then echo "STABILITY_API_KEY: present"; else echo "STABILITY_API_KEY: missing"; exit 1; fi'
```

If output is `missing`, run:

```bash
doppler setup
doppler run -- sh -lc 'if [ -n "$STABILITY_API_KEY" ]; then echo "STABILITY_API_KEY: present"; else echo "STABILITY_API_KEY: missing"; exit 1; fi'
```

## Execution Policy

- Run all env-dependent commands as `doppler run -- <command>`.
- If a command fails without Doppler, retry once with `doppler run --`.
- Include both `pwd` and the exact command in error reports.
- Do not report an env blocker until the secret check above has been run in the current shell session.

## Examples

```bash
doppler run -- .venv/bin/python scripts/validate_env.py
doppler run -- .venv/bin/python scripts/run_first_e2e_recipe.py
```
