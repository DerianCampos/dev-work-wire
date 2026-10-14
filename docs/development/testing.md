# Testing Guide

## Test Structure

Tests mirror the source directory structure:

```
tests/
├── unit/
│   ├── config/
│   │   ├── test_app_config.py       # AppConfig singleton, env selection, env-var interpolation
│   │   └── test_project_config.py   # devworkwire.yml loading and validation
│   ├── core/
│   │   ├── test_composition.py      # Container builds JiraSettings from config; caches the provider
│   │   └── test_work_actions.py     # Story patches, filter composition, assignment and transition selection
│   ├── domain/
│   │   ├── test_entities.py         # Epic / UserStory construction and validation
│   │   └── test_value_objects.py    # IssueId / Priority / StoryPoints / Label
│   ├── import_/
│   │   └── test_markdown_parser.py  # epic.md / stories.md parsing
│   ├── jira/
│   │   ├── test_jira_provider.py    # JiraProvider against respx-mocked HTTP (incl. retry/timeout/api_version wiring)
│   │   ├── test_jira_changes.py     # Edit/assignment/transition payloads, uncertain writes and search pagination
│   │   └── test_markdown_to_adf.py  # Markdown description -> Atlassian Document Format conversion
│   ├── presentation/
│   │   ├── test_banner.py           # Startup banner rendering
│   │   ├── test_epic_panel.py       # Bordered epic/story detail panels
│   │   ├── test_interactive_menu.py # Interactive menu wiring (selection -> prompt -> action), mocked
│   │   ├── test_menu_view.py        # Menu screen renderer and keyboard shortcuts
│   │   └── test_work_menu.py        # Manage/search prompts, clearing, confirmation and cancellation
│   └── shared/
│       └── test_log_config.py       # Structured logging formatters and context
└── integration/
    ├── test_cli.py                  # Typer commands end-to-end via CliRunner + respx
    └── test_work_commands.py        # Edit, assignment, transition and search commands in text/JSON
```

## Running Tests

```bash
# All tests
pytest

# With verbose output
pytest -v

# Specific file
pytest tests/unit/jira/test_jira_provider.py

# Specific test
pytest tests/unit/domain/test_entities.py::test_create_epic

# With coverage
pytest --cov=src
```

## Writing Tests

### Domain Tests

Domain tests are pure unit tests — no mocks, no I/O:

```python
def test_create_epic():
    epic = Epic.create(title="My Epic", description="Desc")
    assert epic.title == "My Epic"

def test_create_epic_empty_title():
    with pytest.raises(BusinessRuleViolation):
        Epic.create(title=" ", description="")
```

### Jira Adapter Tests

Use `respx` to mock HTTP responses against the real `JiraProvider`:

```python
@pytest.mark.asyncio
@respx.mock
async def test_fetch_epic(provider):
    respx.get("https://jira.example.com/rest/api/3/issue/PROJ-1").mock(
        return_value=httpx.Response(
            200,
            json={"key": "PROJ-1", "fields": {"summary": "S", "issuetype": {"name": "Epic"}}},
        )
    )
    epic = await provider.fetch_epic("PROJ-1")
    assert epic.title == "S"
```

Keep `max_retries=1` (or similarly low) in test fixtures for `JiraSettings` — `backoff`'s exponential delays otherwise slow the suite for every test that exercises a failure path.

### CLI Tests

`tests/integration/test_cli.py` invokes the Typer app through `CliRunner`, injecting a `JiraProvider` directly into the module-level `container` (bypassing real config loading):

```python
@pytest.fixture
def mock_config():
    container._jira_provider = JiraProvider(JiraSettings(..., max_retries=1))
    yield
    container._jira_provider = None
```

Cover both success and error paths. Commands print errors to stderr and return a nonzero exit status, including when a folder import creates only some stories.

### Interactive Menu Tests

The interactive menu (`presentation/cli/main.py::_run_interactive_menu`) is tested by monkeypatching its menu selection and `InquirerPy.inquirer.text` prompts, and the async action helpers with `unittest.mock.AsyncMock`. The screen renderer and keyboard shortcuts have focused tests in `test_menu_view.py`. End-to-end keyboard behavior is also verified manually against a pty (`script -q /dev/null dwire` or similar).

## Testing Conventions

- Use `pytest` fixtures for shared setup.
- Use `pytest.mark.asyncio` for async test functions.
- Use `pytest.raises` for exception testing.
- Mock at the HTTP boundary (`respx`) or the provider/config boundary — never mock domain objects.
- Each test asserts one behavior.
