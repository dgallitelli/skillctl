---
name: tdd-workflow
description: Guides agents through test-driven development using the red-green-refactor cycle before writing implementation code
skillctl:
  namespace: examples
  version: 1.0.0
  category: testing
  tags:
    - tdd
    - testing
    - workflow
  capabilities:
    - read_file
    - write_file
---

# TDD Workflow

Guide every feature and bugfix through the red-green-refactor cycle.

## When to activate

Activate when the user asks to implement a feature, fix a bug, or add behavior — before any implementation code is written.

Do NOT activate for documentation changes, configuration tweaks, refactoring that preserves behavior, or exploratory questions.

## The cycle

### 1. Red — Write a failing test

Before touching implementation code, write a test that captures the desired behavior:

- One test per behavior. Test what the code should do, not how it does it.
- Run the test and confirm it fails for the right reason.
- If the test passes immediately, the behavior already exists — stop and reassess.

### 2. Green — Make it pass

Write the minimum implementation code to make the failing test pass:

- Do not add unrelated functionality.
- Do not optimize. Do not refactor. Just make it green.
- Run the full test suite to confirm nothing else broke.

### 3. Refactor — Clean up

With all tests passing, improve the code:

- Remove duplication between the new code and existing code.
- Improve naming, extract helpers, simplify conditionals.
- Run tests after each change to ensure they still pass.

## Rules

- Never skip the red step. A test that was never red proves nothing.
- Never write implementation before the test exists.
- Keep tests focused: one logical assertion per test. Multiple `assert` calls are fine if they verify the same behavior.
- If a bug report arrives, reproduce it as a failing test first, then fix.
- If you discover missing test coverage during refactoring, add the test in a separate red-green cycle before continuing.

## Test naming

Use descriptive names that read as specifications:

```
test_empty_cart_has_zero_total
test_adding_item_increases_count
test_removing_last_item_empties_cart
test_discount_code_reduces_total_by_percentage
```

Avoid generic names like `test_function_works` or `test_case_1`.

## Edge cases to cover

After the primary behavior passes, add tests for:

- Empty inputs and boundary values
- Error conditions and invalid arguments
- Concurrent access (if applicable)
- Integration points with external systems (mocked at unit level)
