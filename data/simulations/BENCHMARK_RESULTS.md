# Dialogue Benchmark Results

Generated: 2026-02-26T13:37:32.271507

## Ranked Models / Configs
- **openai/gpt-4.1-mini-2025-04-14**: combined=88.0 (judge=85.0, qa=100.0, runs=1)
- **openai/gpt-4-1106-preview**: combined=84.0 (judge=80.0, qa=100.0, runs=4)
- **openai/gpt-4.1-mini**: combined=80.0 (judge=75.0, qa=100.0, runs=4)
- **openai/gpt-4.1**: combined=80.0 (judge=75.0, qa=100.0, runs=4)
- **openai/gpt-4-turbo**: combined=80.0 (judge=75.0, qa=100.0, runs=4)
- **openai/gpt-4-0613**: combined=80.0 (judge=75.0, qa=100.0, runs=4)
- **openai/gpt-4-turbo-preview**: combined=78.0 (judge=72.5, qa=100.0, runs=4)
- **openai/gpt-4-turbo-2024-04-09**: combined=78.0 (judge=72.5, qa=100.0, runs=4)
- **openai/gpt-4**: combined=78.0 (judge=72.5, qa=100.0, runs=4)
- **ollama/qwen3:32b**: combined=77.33 (judge=71.67, qa=100.0, runs=3)
- **openai/gpt-3.5-turbo-1106**: combined=77.0 (judge=71.25, qa=100.0, runs=4)
- **openai/gpt-4.1-2025-04-14**: combined=76.0 (judge=70.0, qa=100.0, runs=4)
- **openai/gpt-3.5-turbo-16k**: combined=76.0 (judge=70.0, qa=100.0, runs=4)
- **openai/gpt-3.5-turbo-0125**: combined=76.0 (judge=70.0, qa=100.0, runs=4)
- **openai/gpt-3.5-turbo**: combined=76.0 (judge=70.0, qa=100.0, runs=4)
- **openai/gpt-4-0125-preview**: combined=75.0 (judge=68.75, qa=100.0, runs=4)

## Best Pairings
- Margaret Chen and Julian Torres: mentioned 4 runs
- Margaret Chen & Stephanie Whitmore: mentioned 2 runs
- Marcus Reid and Margaret Chen: mentioned 1 runs
- Margaret Chen & Marcus Reid: mentioned 1 runs
- Margaret Chen and Stephanie 'Steph' Whitmore: mentioned 1 runs
- Margaret Chen & Stephanie 'Steph' Whitmore: mentioned 1 runs

## Weak Characters
- Margaret Chen: flagged 8 runs
- Stephanie 'Steph' Whitmore: flagged 3 runs
- Devon Park: flagged 1 runs
- Julian Torres: flagged 1 runs

## Recommended Config
- Primary: **openai/gpt-4.1-mini-2025-04-14**
- Guardrails: enforce prompt-echo hard fail + min-content + cross-character overlap penalties.
- Judge pass: keep `openai/gpt-4o` for final weekly grading.
