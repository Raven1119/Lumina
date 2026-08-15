# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for all
operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`
- **Read an issue**: `gh issue view <number> --comments`
- **List issues**: use `gh issue list` with appropriate state and label filters.
- **Comment**: `gh issue comment <number> --body "..."`
- **Apply/remove labels**: `gh issue edit <number> --add-label "..."` or
  `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repository from `git remote -v`; `gh` does this automatically inside
the clone.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub issues are the canonical request and triage surface. A bare `#42` may
refer to an issue or PR; resolve it with `gh pr view 42`, falling back to
`gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

The `/wayfinder` map is one issue with child issues as tickets.

- Label maps `wayfinder:map`.
- Label child tickets `wayfinder:research`, `wayfinder:prototype`,
  `wayfinder:grilling`, or `wayfinder:task`.
- Use native GitHub sub-issues and dependencies when available.
- Fall back to task lists and `Blocked by: #<n>` when unavailable.
- Claim work with `gh issue edit <n> --add-assignee @me`.
- Resolve by commenting, closing the child, and recording its context pointer
  in the map.
