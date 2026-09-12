---
name: git_master
description: Advanced Git workflows, branch strategies, conflict resolution, rebase operations, and conventional commit generation.
triggers: [git, branch, commit, rebase, merge conflict, pr, pull request, cherry-pick]
---
# Git Master for Harness

Expert guidance for version control operations, atomic commits, branch hygiene, and conflict resolution.

## Core Best Practices
1. **Never Commit Broken Code**: Always run tests and lints prior to staging or committing changes.
2. **Conventional Commits**: Format commit messages according to the Conventional Commits specification:
   - `feat(scope): add new capability`
   - `fix(scope): resolve issue description`
   - `refactor(scope): restructure without changing external behavior`
   - `test(scope): add or improve test coverage`
   - `docs(scope): update documentation`
3. **Conflict Resolution**:
   - Inspect conflicting files with `view_file` to locate `<<<<<<<`, `=======`, and `>>>>>>>` markers.
   - Analyze incoming vs current changes carefully.
   - Use `edit_file` to resolve conflicts cleanly without leaving residual markers.
   - Run tests before staging the resolved file.
