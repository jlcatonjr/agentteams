# Goose Cloud Provider Guide

How to connect Goose to cloud-hosted models instead of (or alongside) a local Ollama instance.

---

## Why Cloud Compute

| Situation | Recommendation |
|---|---|
| Need the strongest available model | OpenRouter → `anthropic/claude-opus-4.1` or `google/gemini-2.5-pro` (exact availability varies by provider — see the model tables below) |
| Cost-optimised, high throughput | OpenRouter → `deepseek/deepseek-r1-0528` or `qwen/qwen3-coder` |
| Direct Anthropic billing | `anthropic` provider |
| Enterprise / org key management | `azure_openai`, `aws_bedrock`, or `databricks` |
| Many providers, one bill | `openrouter` or `litellm` proxy |
| Privacy-first, no data egress | Local Ollama (default) |

---

## Quick Setup: Interactive

The easiest way is the interactive configurator:

```bash
goose configure
```

At the prompt **"How would you like to set up your provider?"**, choose:
- **OpenRouter Login (Recommended)** — browser OAuth, no API key to manage
- **Manual Configuration** — choose provider and paste API key

Goose writes the result to `~/.config/goose/config.yaml` and verifies connectivity before finishing.

---

## OpenRouter

[openrouter.ai](https://openrouter.ai) aggregates most major model providers under a single API key and billing account. It is the most flexible starting point for cloud use.

### Step 1 — Get an API key

1. Sign up at openrouter.ai
2. Go to **Settings → Keys → Create**
3. Copy the key (starts with `sk-or-v1-...`)

### Step 2 — Set the environment variable

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

Add to `~/.zshrc` or `~/.bashrc` to persist across sessions.

### Step 3 — Configure goose

**Option A — interactive (recommended):**

```bash
goose configure
# Choose: Manual Configuration → OpenRouter → paste key → select model
```

**Option B — edit config.yaml directly:**

```yaml
# ~/.config/goose/config.yaml
GOOSE_PROVIDER: openrouter
GOOSE_MODEL: anthropic/claude-sonnet-4.5
OPENROUTER_API_KEY: sk-or-v1-...   # only if not using env var
```

### OpenRouter model names

Use OpenRouter's `provider/model` format. Confirmed models in Goose 1.37:

| Model | ID |
|---|---|
| Claude Sonnet 4.5 | `anthropic/claude-sonnet-4.5` |
| Claude Sonnet 4 | `anthropic/claude-sonnet-4` |
| Claude Opus 4.1 | `anthropic/claude-opus-4.1` |
| Claude Opus 4 | `anthropic/claude-opus-4` |
| Gemini 2.5 Pro | `google/gemini-2.5-pro` |
| Gemini 2.5 Flash | `google/gemini-2.5-flash` |
| DeepSeek R1 (0528) | `deepseek/deepseek-r1-0528` |
| Qwen3 Coder | `qwen/qwen3-coder` |
| Kimi K2 | `moonshotai/kimi-k2` |
| Grok Code Fast | `x-ai/grok-code-fast-1` |

For the full current list: `goose session` → `/model` → pick OpenRouter → search.

### Per-session model override

```bash
# Start a session with a specific OpenRouter model without changing your default
goose run --recipe .goose/recipes/orchestrator.yaml \
  --provider openrouter \
  --model anthropic/claude-opus-4
```

---

## Direct Providers

Configure these if you prefer to bill the provider directly rather than through OpenRouter.

### Anthropic

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

```yaml
GOOSE_PROVIDER: anthropic
GOOSE_MODEL: claude-sonnet-4-6
```

Models available in Goose: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-sonnet-4-5-20250929`, `claude-haiku-4-5-20251001`, `claude-opus-4-5-20251101`.

Custom endpoint (e.g., Anthropic-compatible proxy):

```yaml
ANTHROPIC_HOST: https://your-proxy.example.com
ANTHROPIC_API_KEY: your-key
```

Custom headers:

```yaml
ANTHROPIC_CUSTOM_HEADERS: '{"x-origin-client-id": "my-app"}'
```

### OpenAI

```bash
export OPENAI_API_KEY="sk-..."
```

```yaml
GOOSE_PROVIDER: openai
GOOSE_MODEL: gpt-4o
```

Optional overrides:

```yaml
OPENAI_BASE_URL: https://your-openai-compatible-endpoint.com  # for compatible APIs
OPENAI_ORGANIZATION: org-...
OPENAI_PROJECT: proj-...
OPENAI_TIMEOUT: 120
```

### Google Gemini

```bash
export GOOGLE_API_KEY="AIza..."
```

```yaml
GOOSE_PROVIDER: google
GOOSE_MODEL: gemini-2.5-pro
```

For OAuth (sign-in via browser):

```bash
goose configure
# Choose: Google Gemini → Sign in with Google
```

### Mistral

```bash
export MISTRAL_API_KEY="..."
```

```yaml
GOOSE_PROVIDER: mistral
GOOSE_MODEL: mistral-large-latest
```

### xAI (Grok)

```bash
export XAI_API_KEY="xai-..."
```

```yaml
GOOSE_PROVIDER: xai
GOOSE_MODEL: grok-3
```

Available: `grok-4-0709`, `grok-3`, `grok-3-fast`, `grok-3-mini`, `grok-2-vision-1212`.

### Groq

Groq runs open-weight models (Llama, Qwen, etc.) on fast LPU hardware — very low latency.

```bash
export GROQ_API_KEY="gsk_..."
```

```yaml
GOOSE_PROVIDER: groq
GOOSE_MODEL: llama-3.3-70b-versatile
```

### DeepSeek

```bash
export DEEPSEEK_API_KEY="sk-..."
```

```yaml
GOOSE_PROVIDER: custom_deepseek
GOOSE_MODEL: deepseek-chat
```

---

## Enterprise / Infrastructure Providers

### Azure OpenAI

```bash
export AZURE_OPENAI_API_KEY="..."
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com"
export AZURE_OPENAI_DEPLOYMENT_NAME="gpt-4o"
```

```yaml
GOOSE_PROVIDER: azure_openai
GOOSE_MODEL: gpt-4o
```

### AWS Bedrock

Uses your existing AWS credential chain (SSO, environment, or credentials file). Region is required.

```bash
export AWS_REGION="us-east-1"
# Use SSO: aws sso login --profile my-profile
# Or: export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
```

```yaml
GOOSE_PROVIDER: aws_bedrock
GOOSE_MODEL: anthropic.claude-sonnet-4-5-20250929-v1:0
```

Enable prompt caching for Anthropic models on Bedrock:

```bash
export BEDROCK_ENABLE_CACHING=true
```

### GCP Vertex AI

```bash
export GCP_PROJECT_ID="my-gcp-project"
export GCP_LOCATION="us-central1"
```

```yaml
GOOSE_PROVIDER: gcp_vertex_ai
GOOSE_MODEL: gemini-2.5-pro
```

### Databricks

```bash
export DATABRICKS_HOST="https://dbc-....cloud.databricks.com"
export DATABRICKS_TOKEN="dapi..."
```

```yaml
GOOSE_PROVIDER: databricks
GOOSE_MODEL: databricks-claude-sonnet-4-5
```

Available: `databricks-claude-sonnet-4-5`, `databricks-meta-llama-3-3-70b-instruct`, `databricks-meta-llama-3-1-405b-instruct`.

For Databricks AI Gateway v2 endpoints: use `GOOSE_PROVIDER: databricks_v2`.

### LiteLLM Proxy

LiteLLM is a self-hosted proxy that normalises many providers behind one OpenAI-compatible endpoint — useful for teams that want centralized key management or usage tracking.

```bash
export LITELLM_HOST="https://your-litellm-proxy.example.com"
export LITELLM_API_KEY="..."
```

```yaml
GOOSE_PROVIDER: litellm
GOOSE_MODEL: claude-sonnet-4-5   # as configured in your proxy
```

---

## Switching Providers

### Keep multiple providers configured, switch default

Goose now supports a `providers:` block that stores settings for each provider independently, so switching doesn't overwrite your model selection for the other provider.

```yaml
# ~/.config/goose/config.yaml
GOOSE_MODE: auto
GOOSE_PROVIDER: openrouter       # active default
GOOSE_MODEL: anthropic/claude-sonnet-4.5

extensions:
  developer:
    enabled: true
    type: builtin
    name: developer
    timeout: 300
    bundled: true
```

To change default interactively: `goose session` → `/model` → pick a different provider and model → press Enter to save as default (or `s` for session-only).

### Override for one run

```bash
# Cloud model for a single recipe run, local default unchanged
goose run --recipe .goose/recipes/orchestrator.yaml \
  --provider openrouter \
  --model deepseek/deepseek-r1-0528

# Override inside a live session
/model openrouter anthropic/claude-opus-4
```

### Override via environment variable

Env vars take precedence over config.yaml for the duration of the shell session:

```bash
GOOSE_PROVIDER=anthropic GOOSE_MODEL=claude-opus-4-6 goose session
```

Useful in CI or scripts where you don't want to alter the shared config file.

---

## Verifying Your Configuration

```bash
# Show active provider and model
goose info --verbose

# Run connectivity check
goose doctor

# Test from a session
goose session
> /status
```

A successful `goose doctor` shows the active provider connected and the model responding.

---

## Config.yaml Reference

Full example with OpenRouter as default and Anthropic as a fallback entry:

```yaml
# ~/.config/goose/config.yaml

GOOSE_PROVIDER: openrouter
GOOSE_MODEL: anthropic/claude-sonnet-4.5
GOOSE_MODE: auto

# Extension defaults
extensions:
  developer:
    enabled: true
    type: builtin
    name: developer
    timeout: 300
    bundled: true

# Secrets can go here or in env vars (env vars take precedence)
# OPENROUTER_API_KEY: sk-or-v1-...
# ANTHROPIC_API_KEY: sk-ant-...
```

> **Security note:** Storing API keys in `config.yaml` writes them to disk in plaintext. Prefer environment variables or the system keyring (`goose configure` → Secret Storage → System Keyring).

---

## Provider Quick Reference

| Provider ID | API key env var | Key source |
|---|---|---|
| `openrouter` | `OPENROUTER_API_KEY` | openrouter.ai/settings/keys |
| `anthropic` | `ANTHROPIC_API_KEY` | console.anthropic.com/settings/keys |
| `openai` | `OPENAI_API_KEY` | platform.openai.com/api-keys |
| `google` | `GOOGLE_API_KEY` | aistudio.google.com/apikey |
| `mistral` | `MISTRAL_API_KEY` | console.mistral.ai/api-keys |
| `groq` | `GROQ_API_KEY` | console.groq.com/keys |
| `xai` | `XAI_API_KEY` | x.ai/grok |
| `custom_deepseek` | `DEEPSEEK_API_KEY` | platform.deepseek.com/api_keys |
| `azure_openai` | `AZURE_OPENAI_API_KEY` + endpoint/deployment | Azure portal |
| `aws_bedrock` | AWS credential chain | AWS console |
| `gcp_vertex_ai` | `GCP_PROJECT_ID` + `GCP_LOCATION` | Google Cloud console |
| `databricks` | `DATABRICKS_HOST` + `DATABRICKS_TOKEN` | Databricks workspace |
| `litellm` | `LITELLM_HOST` + `LITELLM_API_KEY` | Your LiteLLM proxy |
| `nvidia` | API key from build.nvidia.com | NVIDIA build console |
| `snowflake` | `SNOWFLAKE_HOST` + `SNOWFLAKE_TOKEN` | Snowflake console |
| `ollama` | `OLLAMA_HOST` (default: localhost:11434) | Local — no key needed |
