---
name: code_refactor
description: Structural code refactoring, complexity reduction, design pattern application, and dead code cleanup while preserving functional parity.
triggers: [refactor, cleanup, decouple, simplify, dry, code smell, dead code]
---
# Code Refactor for Harness

Expert guidance for safely restructuring code to improve readability, maintainability, and testability.

## Refactoring Tenets
1. **Never Change Behavior While Refactoring**:
   - Verify existing test suite passes BEFORE beginning refactoring.
   - Make small, incremental changes rather than massive rewrites.
   - Run tests after each atomic edit.
2. **Key Target Areas**:
   - Extract Method / Function: Break down 100+ line functions into single-responsibility helpers.
   - Eliminate Duplication (DRY): Factor shared logic into reusable utilities.
   - Replace Magic Literals: Use typed constants or enums.
   - Flatten Nested Conditionals: Use early return / guard clauses.
