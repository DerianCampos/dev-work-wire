# Configuration

DevWorkWire resolves its Jira connection (base URL, email, API token, project key) from three sources, highest priority first:

- **Connection** (base URL, email, API token): environment variables (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, including those from a `.env` file) override the stored user config written by `dwire config setup`.
- **Project key**: `--project KEY` for the run, else the stored default project, else `project_key` in `devworkwire.yml`. There is no `JIRA_PROJECT_KEY` variable.

Logging and request settings (timeout, retries) come from `config_{env}.yml`, selected by `APP_ENV`.

## Stored Jira settings (`dwire config`)

The easiest way to configure Jira is to let the CLI store it for you; no file editing needed:

```bash
dwire config setup               # base URL, email, API token (hidden) and your first project
dwire config add-project OTHER   # add more projects; --default makes it the default
dwire config set-default OTHER   # choose the project used when --project is not given
dwire config remove-project OTHER
dwire config show                # effective values, where each comes from, token masked
dwire config clear               # delete everything stored (asks first; --yes to skip)
```

One Jira connection serves all your projects. The first project you add becomes the default; removing the default promotes the oldest remaining one. Pick another project for a single run with the root option:

```bash
dwire --project OTHER list-assigned
```

`--project` must name a configured project (typos fail with a hint to `add-project`); it is accepted as-is only when no projects are stored.

Settings live in a SQLite database at `~/.config/devworkwire/config.db` (`$XDG_CONFIG_HOME/devworkwire/` if set, or `$DEVWORKWIRE_CONFIG_DIR`). The directory is created `0700` and the file `0600`; permissions of an existing directory are tightened to `0700` too, so do not point `DEVWORKWIRE_CONFIG_DIR` at a shared folder such as `$HOME`. **The API token is stored in plaintext**, at the same trust level as a `.env` file, and is never printed. `setup` always prompts for the token, so it never lands in your shell history; pressing Enter at the token prompt keeps the stored one.

Because environment variables win for the connection, an existing `.env` keeps working unchanged. A stored default project beats the `project_key` in `devworkwire.yml`; with no stored projects, `devworkwire.yml` still works as before. If the stored config cannot be read (for example a corrupt file), environment-only setups keep working and `config show` prints a warning.

## Environment Variables (`.env`)

Optional if you used `dwire config setup`. To use a `.env` file, create it in the project root. Start from the template:

```bash
cp .env.example .env
```

### Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `APP_ENV` | Environment name, selects the YAML config file | `local` |
| `JIRA_BASE_URL` | Your Jira instance URL | `https://your-domain.atlassian.net` |
| `JIRA_EMAIL` | Email associated with your Jira account | `you@example.com` |
| `JIRA_API_TOKEN` | API token from Atlassian | `ATATT3...` |

### How `APP_ENV` Works

The value of `APP_ENV` determines which YAML config file is loaded:

| `APP_ENV` | Config File Loaded | Use Case |
|-----------|-------------------|----------|
| `local` | `src/devworkwire/config/config_local.yml` | Local development |
| `test` | `src/devworkwire/config/config_test.yml` | Running tests |
| `dev` | `src/devworkwire/config/config_dev.yml` | Shared dev environment |
| `prod` | `src/devworkwire/config/config_prod.yml` (create if needed) | Production |

## YAML Configuration

The YAML files live in `src/devworkwire/config/` and provide structured settings. Here's what `config_local.yml` looks like:

```yaml
app:
  name: "DevWorkWire"
  version: "0.1.0"

logging:
  level: "debug"
  format_type: "text"
  console:
    enabled: true
    colored: true

jira:
  api_version: "3"
  timeout: 30
  max_retries: 3
```

### Logging Settings

| Setting | Description | Default |
|---------|-------------|---------|
| `logging.level` | Log verbosity (`debug`, `info`, `warning`, `error`) | `info` |
| `logging.format_type` | Output format: `text` (plain, human-readable) or `json` (single-line structured) | `json` |

Use `format_type: "text"` for local and container runs so logs are easy to read. Use `format_type: "json"` for dev/prod so log aggregators can parse structured fields. Every record carries `request_id` and `user_id` correlation IDs, plus any `extra={...}` fields passed at the call site.

### Key Jira Settings

| Setting | Description | Default |
|---------|-------------|---------|
| `jira.api_version` | Jira REST API version | `3` |
| `jira.timeout` | Request timeout (seconds) | `30` |
| `jira.max_retries` | Max attempts for transient failures (read requests only) | `3` in the shipped YAML files (`4` if the key is omitted) |

The Jira URL, email, token and project key are not in these files; see the resolution order above. The `!ENV ${VAR_NAME}` syntax is still supported in YAML files for any other value you want to pull from the environment.

## Project Configuration (`devworkwire.yml`)

This file tells DevWorkWire which provider serves the project and the project key in that provider. Place it in the directory where you run `dwire` (there is no CLI flag yet to point at a different path).

Copy the example and adjust:

```bash
cp devworkwire.example.yml devworkwire.yml
```

```yaml
provider: jira
project_key: PROJ
field_mappings:
  story_points: customfield_10011
```

| Field | Description | Default |
|-------|-------------|---------|
| `provider` | Provider adapter to use (Phase 1: `jira` only) | **required** |
| `project_key` | Project key for issue creation; used only when no project is stored with `dwire config` | optional |
| `field_mappings.story_points` | Custom field ID for story points | `customfield_10011` |
| `transition_overrides` | Map DevWorkWire transition names to provider-specific names | `{}` |

`transition_overrides` maps aliases accepted by `transition --status` to Jira
workflow **action names**, rather than destination status names:

```yaml
transition_overrides:
  start: Start Progress
  finish: Close Issue
```

For example, `dwire transition PROJ-124 --status start` selects the available
`Start Progress` action. Alias keys and action names match case-insensitively.
Without an alias, `--status` matches the destination status. Ambiguous matches
fail without writing; `--transition-id` selects an available ID directly and
bypasses aliases. Use `dwire list-transitions PROJ-124` to discover names and IDs.

## Generating a Jira API Token

1. Go to [Atlassian API tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**
3. Give it a label (e.g., "DevWorkWire")
4. Copy the token — you won't be able to see it again
5. Paste it at the `dwire config setup` token prompt (or into `.env` as `JIRA_API_TOKEN`)

## Troubleshooting

**"Jira is not configured (missing: …)"**
→ Run `dwire config setup`, or set the listed `JIRA_*` environment variables. `dwire config show` lists what is missing.

**"Configuration file not found"**
→ Check that `APP_ENV` matches an existing config file name. For `APP_ENV=local`, the file must be `config_local.yml`.

**"Jira API returned 401"**
→ Verify `JIRA_EMAIL` and `JIRA_API_TOKEN` are correct. Make sure the API token hasn't expired.

**Values come from the wrong place**
→ Run `dwire config show`; the source column says whether each value comes from `env`, `db` or `devworkwire.yml`. Environment variables (including `.env`) override the stored settings.
