# Agent Instructions for bustimes.org

## Build/Lint/Test Commands

### Python/Django
- **Install deps**: `uv sync`
- **Run all tests**: `python manage.py test`
- **Run single test**: `python manage.py test app.tests.TestClass.test_method`
- **Lint**: `ruff check . && ruff format .`
- **Type check**: `python manage.py check`

### JavaScript/TypeScript
- **Install deps**: `npm install`
- **Build**: `npm run build`
- **Watch**: `npm run watch`
- **Test**: `npm test`
- **Lint**: `npm run lint` (TypeScript type checking)

## Code Style Guidelines

### Python
- Use `ruff` for linting and formatting (4 spaces, black-compatible)
- Follow Django conventions: models in `models.py`, views in `views.py`
- Use type hints where beneficial
- Import order: stdlib, Django, third-party, local
- Use descriptive variable names, snake_case for functions/variables
- Handle exceptions appropriately, avoid bare `except:`

### JavaScript/TypeScript
- Use `biome` for linting (space indentation)
- Strict TypeScript mode enabled
- React with JSX, functional components preferred
- Use descriptive names, camelCase for variables/functions
- Import order: React, third-party, local components/utils
- Prefer const/let over var, arrow functions

### General
- No comments unless explaining complex business logic
- Follow existing patterns in codebase
- Run pre-commit hooks before committing: `pre-commit run --all-files`