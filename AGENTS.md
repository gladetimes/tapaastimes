# Agent Instructions for gladetimes

## Build/Lint/Test Commands
### Python/Django (Python >=3.13)
- Install: `uv sync --group dev --group test`
- All tests: `python manage.py test`
- Single test: `python manage.py test app.tests.TestClass.test_method`
- Lint/format: `ruff check . && ruff format .` (unsafe-fixes enabled)
- Type check: `python manage.py check`

### JavaScript/TypeScript
- Install: `npm install`
- Build/watch: `npm run build` / `npm run watch`
- Test: `npm test`
- Lint: `tsc -noEmit` (strict mode)

## Code Style Guidelines
### Python
- `ruff` formatting (4 spaces, black-compatible), snake_case, type hints where beneficial
- Import order: stdlib → Django → third-party → local
- Proper exception handling, avoid bare `except:`, separate models/views files

### JavaScript/TypeScript
- `biome` linting (space indentation, strict mode), camelCase, const/let over var
- React functional components, JSX, arrow functions, no parameter assignment/expression assignment
- Import order: React → third-party → local
- Parameter assignment and expression assignment warnings enabled

### General
- No comments unless complex business logic
- Run pre-commit hooks: `pre-commit run --all-files` (includes ruff, biome, djade, trailing-whitespace, end-of-file-fixer)