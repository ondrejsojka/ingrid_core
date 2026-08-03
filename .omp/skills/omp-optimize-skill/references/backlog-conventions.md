# Backlog Conventions

These conventions apply when the project maintains a performance investigation backlog (e.g., a `PERFORMANCE_INVESTIGATIONS.md` file). If the project has no such file, the orchestrator may create one following these conventions.

## Status Annotations

Annotations are appended to backlog items using bold text:

- **Implemented — ~X% improvement.** See [report](<path-to-report>). Item is complete with measured result.
- **Investigated — <conclusion>.** See [report](<path-to-report>). Item was investigated but not worth pursuing (explain why).
- **Partially addressed — <what was done>.** See [report](<path-to-report>). Some aspect was addressed but further work remains.
- **Superseded** — see [report](<path-to-report>). Another approach made this item irrelevant.

## Strikethrough

Use ~~strikethrough~~ on the original item text when it is fully resolved (implemented or superseded). Keep the annotation visible after the strikethrough.

## Sub-investigations

When an investigation spawns a focused follow-up, add it as an indented sub-item with its own annotation and link.

## Link Format

Use relative links from the project root to investigation reports.

## Adding New Items

New backlog items from investigation "Remaining opportunities" sections should be added to the appropriate category section. Only promote to a "High Priority" section if there is evidence of high ROI.

## Adapting to Project Conventions

If the project already has a performance backlog with its own conventions, follow those instead. These conventions are a default for projects without established patterns.
