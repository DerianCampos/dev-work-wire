# CLI Reference

DevWorkWire provides direct commands for scripts and an interactive menu for one-off work.

## Launching the CLI

```bash
# Installed package

dwire

# Source checkout
./scripts/run-cli

# Python module
python -m devworkwire.presentation.cli
```

Running `dwire` without a command opens the menu. Direct commands run without the menu or banner.

Use the root option `--theme NAME` to choose a palette for one run, or `dwire config theme NAME` to save it. Choices are `dark` (default), `light`, `dracula`, `solarized`, and `monochrome`. A nonempty `NO_COLOR` environment variable uses Monochrome. See [CLI themes](../getting-started/configuration.md#cli-themes) for storage and precedence.

## JSON output for tools and scripts

Place `--format json` before any direct command to receive one JSON object on stdout.
The interactive menu remains text-only. Diagnostic logs go to stderr. A failed command
still exits nonzero, so scripts should check both the exit status and the JSON result.

```bash
dwire --format json fetch-story PROJ-124
dwire --format json preview-folder path/to/work-items
dwire --format json import-folder path/to/work-items --yes
```

Every result has `command`, `status`, `data`, and `error`. `error` is `null` on
success; otherwise it contains a stable `code` and a human-readable `message`.
Argument errors detected before a command runs use the `INVALID_ARGUMENT`
code and still exit nonzero.
`status` is `completed` for a successful read or write, `preview` for a valid
read-only preview, `no_change` for an already imported folder, `error` for a
failed command with no new items, or `partial` when an import created some items
but did not complete. The `data` object contains work-item fields, a preview,
or created keys as appropriate. Preview actions are `create`, `skip`, and
`unresolved`; `changed: true` on a skipped item means its Markdown differs from
the recorded upload, and the importer will **not** update that Jira item.

JSON mode never prompts. `import-folder` requires `--yes` when it has work to
create. Use it only for a user-authorized write; the CLI cannot inspect an AI
agent's task approval. A `partial` result requires inspecting the created keys
and unresolved state before retrying.

## Interactive Menu

Use the arrow keys and Enter to choose an action, or press its number (`1`–`9`) to select it directly. Press `s` for **Search work items**, `w` for **Manage work items**, or `?` for keyboard help. Esc leaves the main menu or returns from help; `0` selects **Exit**. The layout adapts to smaller terminals.

Manage Work offers story editing, assignment, status changes, and transition listing.
Story editing asks which fields to change; unselected fields remain untouched.
Optional fields can be cleared explicitly. Assignment offers the current user,
an account ID, or unassigned. Status changes show available Jira action names,
destination statuses, and IDs. These menu writes require confirmation; Back or
Ctrl-C returns without writing. Search offers comma-separated filters or raw JQL.

The menu can retrieve an epic or story, list an epic's stories or a user's assigned work, create one epic or story, preview a folder, import it after confirmation, or configure the Jira connection and projects (option `9`, a sub-menu that mirrors `dwire config`: show settings, set up the connection, add a project, choose the default, remove a project). Clearing all stored settings is CLI-only (`dwire config clear`). The assigned-work prompt accepts the provider's account ID; leave it blank for the authenticated user. Creation prompts for title, description, priority, and labels; story creation also prompts for an existing epic key and whole-number points. Labels are comma-separated in the menu. The menu returns after each action.

## Direct Commands

### Create an epic

```bash
dwire create-epic --title "Authentication" --description "Login and recovery" \
  --priority High --label auth --label backend
```

`--title` is required. Description, priority, and repeatable `--label` are optional. This command creates exactly one epic.

### Create a story

```bash
dwire create-story PROJ-123 --title "Log in" --description "A user can log in" \
  --priority High --label auth --points 5
```

The positional key must identify an existing epic. `--title` is required; other options are optional. `--points` accepts a nonnegative whole number and uses the configured `field_mappings.story_points` Jira field.

### Fetch an epic or story

```bash
dwire fetch-epic PROJ-123
dwire fetch-story PROJ-124
```

The detailed view shows key, title, description, priority, and labels when present. Story output also shows parent epic, status, and points when available. Fetch commands reject an issue of the wrong type.

### List an epic's stories

```bash
dwire list-stories PROJ-123
```

Lists every story under the epic as key, title, and status. An empty epic prints a message and succeeds.

### List work assigned to a user

```bash
dwire list-assigned
dwire list-assigned --assignee 557058:abcd-1234
```

Without `--assignee`, the command uses the authenticated Jira user. With it, supply a Jira account ID, not an email address or display name. The command lists open work in the configured project across all issue types, ordered by most recently updated. Each row shows key, issue type, title, and status. “Open” means the Jira status category is not Done. An empty result prints a message and succeeds.

### Update a story

```bash
dwire update-story PROJ-124 --title "Revised login" --description "**Updated** details" \
  --priority High --label backend --label urgent --points 8 --epic PROJ-123
dwire update-story PROJ-124 --description "" --clear-labels --clear-points --clear-epic
```

Only supplied fields are written. At least one change is required. Titles and
priorities cannot be blank; points are nonnegative whole numbers. Descriptions
use the same Markdown-to-ADF conversion as creation. An empty `--description`
clears the description. Repeated `--label` values **replace** existing labels;
`--clear-labels` removes all labels. `--clear-points` removes the estimate, and
`--clear-epic` removes the parent. Clearing options cannot be combined with their
corresponding value options. `--epic` must identify an existing Epic, and the
target must be a Story. Points use the configured `field_mappings.story_points`.
JSON `data` contains `key` and `changed_fields`.

### Assign an issue

```bash
dwire assign PROJ-124 --assignee me
dwire assign PROJ-124 --assignee 557058:abcd-1234
dwire assign PROJ-124 --unassign
```

Assignment supports any issue type. Supply exactly one of `--assignee` or
`--unassign`. Assignees use Jira account IDs, not emails or display names;
`me` resolves the authenticated user. JSON `data` contains `key` and the
resolved `assignee` account ID, or `null` when unassigned.

### List transitions and change status

```bash
dwire list-transitions PROJ-124
dwire transition PROJ-124 --status "In Progress"
dwire transition PROJ-124 --transition-id 31
```

Transition listing shows the available action ID, name, destination status,
and any required screen fields. JSON `data.transitions` contains `id`, `name`,
`status`, and `required_fields` for each action.

Supply exactly one transition selector. `--status` matches destination names
case-insensitively, including names containing spaces. When multiple actions
lead to the same status, or none matches, the command lists the choices and
performs no write. Use `--transition-id` to choose an available action directly.
Existing `transition_overrides` entries provide aliases to Jira action names;
see [Configuration](../getting-started/configuration.md). Jira enforces workflow
rules and permissions. Transition-screen field input is not supported; if Jira
requires additional values, its field error is reported. Successful JSON `data`
contains `key` and the selected `transition`.

### Search work items

```bash
dwire search
dwire --project OTHER search --status "To Do" --status "In Progress" \
  --label backend --label urgent --type Story --type Bug
dwire search --jql 'project IN (PROJ, OTHER) AND statusCategory != Done ORDER BY key ASC'
```

Filter searches use the selected project and include all statuses and issue
types unless filtered. Multiple statuses or types match any specified value;
multiple labels require every specified label. Different filter categories
are combined with AND. Results are ordered by most recently updated, then key.
The CLI retrieves every page. An empty result succeeds.

`--jql` runs the supplied query unchanged, including project scope and ordering;
it can search across accessible projects. It cannot be combined with filter
flags. Jira reports invalid JQL and permission errors. JSON `data` contains
the effective `jql` and `items`, each with `key`, `issue_type`, `title`, and `status`.
Jira search indexing can lag behind a recent write; fetch the issue directly
when checking a change.

### Write acknowledgments and failures

Direct `update-story`, `assign`, and `transition` commands write immediately,
including in JSON mode. Their success output acknowledges Jira's response;
it does not perform an additional read to verify the resulting state. Explicit
issue keys can target accessible issues outside the selected project.

Writes are sent once. A timeout, HTTP 408, or server error returns
`WRITE_UNCERTAIN`: Jira may have applied the change. Inspect the issue before
retrying. Other new-command errors include `VALIDATION_FAILED`, `NOT_FOUND`,
`INVALID_TRANSITION`, `AUTHENTICATION_FAILED`, `PERMISSION_DENIED`, and
`JIRA_ERROR`, with Jira field details retained in the message. Reads retain
the configured retry policy.

### Configure the Jira connection

```bash
dwire config setup [--base-url URL] [--email EMAIL] [--project-key KEY]
dwire config add-project KEY [--default]
dwire config set-default KEY
dwire config remove-project KEY
dwire config show
dwire config theme [dark|light|dracula|solarized|monochrome]
dwire config clear [--yes]
dwire --project KEY <command>
dwire --theme NAME <command>
```

`setup` prompts for anything not passed as an option, and always prompts for the API token (hidden; there is no token option). Re-running it keeps the stored token if you press Enter and keeps your projects. The first project becomes the default; `add-project`, `set-default` and `remove-project` manage the list. `show` prints the effective values with their source (`env`, `db`, `--project` or `devworkwire.yml`), the project list, and masks the token. `clear` deletes the connection and projects after confirmation. The root `--project KEY` option runs any command against another configured project. `setup` is interactive-only, so it fails under `--format json`; the other `config` commands support JSON (`clear` needs `--yes`). See [Configuration](../getting-started/configuration.md) for precedence and storage.

### Preview and validate a folder

```bash
dwire preview-folder path/to/work-items
```

Reads the folder locally without contacting Jira. Shows the epic and each story with compact details, then reports any validation errors with file and line locations. The command exits nonzero if the folder is invalid.
When a resume record exists, the preview also shows Jira keys, items still to create,
changed Markdown, and unresolved attempts. An unresolved attempt makes preview fail.

### Import a folder

```bash
dwire import-folder path/to/work-items
dwire import-folder path/to/work-items --yes  # scripts and other non-interactive runs
```

Reads required `epic.md` and optional `stories.md` from the folder (see [Markdown Format](markdown-format.md)). It previews and validates all local items before creating anything. Invalid folders exit without Jira writes. Interactive runs ask for confirmation; non-interactive runs require `--yes` when there is work to create. A declined prompt creates nothing. After confirmation, the epic is created first, then each story is linked to it. Definite Jira story rejections are reported individually and the remaining stories are attempted; uncertain outcomes stop the import. This command replaces the former `create-epic FOLDER` form.

The importer saves `.devworkwire-import.json` beside the Markdown. It records the
Jira destination and each created key, so rerunning the command skips uploaded
items and creates only missing ones. Story IDs in `### <id>: <title>` headings must
be unique and stable. If an uploaded item's Markdown changes, the CLI warns and
keeps the existing Jira issue; it does not update it. Keep the resume file when
moving the folder. A folder without one is treated as a new import, even if it
was uploaded previously. The file contains Jira keys and the destination URL,
but no credentials, and is ignored by Git.

Issue creation uses one Jira request per item. If the outcome is uncertain
(for example, a timeout or server error), the importer stops with an unresolved
attempt. Check Jira before choosing either resolution:

```bash
dwire resolve-import path/to/work-items --item epic --key PROJ-123
dwire resolve-import path/to/work-items --item US-1 --key PROJ-124
dwire resolve-import path/to/work-items --item US-1 --retry
```

Use `--key` if Jira created the issue; the command verifies its type and, for a
story, its parent epic. Use `--retry` only after confirming Jira created nothing.
The local lock file prevents simultaneous imports of one folder. If a process
crashes and leaves the lock, inspect Jira and the resume file before removing it.

If a recorded story ID is missing from `stories.md`, preview shows its ID and
key and imports stop. After renaming a heading ID, preserve its Jira key with
`dwire rebind-import-story <folder> <old-id> <new-id>`. After intentionally
removing a story from the folder, use
`dwire retire-import-story <folder> <old-id>` to remove its local record.
Retiring does not delete the Jira issue; reusing that ID later can create a
new issue. An unresolved missing story must first be handled with
`resolve-import`.

## Exit Status and Errors

Commands return `0` on success and a nonzero status for missing items, invalid input, Jira errors, a non-interactive import without `--yes`, or any failed story in a folder import. Declining an interactive import exits successfully without creating items. Definite Jira rejections leave those items ready for a later retry; uncertain outcomes require `resolve-import` first. Errors are printed to stderr. Configuration comes from `devworkwire.yml` and the Jira environment settings described in [Configuration](../getting-started/configuration.md).
