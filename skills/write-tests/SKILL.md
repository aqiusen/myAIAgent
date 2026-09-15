---
name: write-tests
description: Design and write tests for existing code. Use when the user asks to add tests, cover a function, or improve test coverage.
---

# Write Tests

Add tests for the code the user pointed to. Match the repository's existing test style.

## How to work

1. Read the target code and the nearest existing tests first.
2. Cover the behavior that callers rely on, then the failure paths.
3. Put tests next to the existing suite, using the same runner and helpers.
4. Do not change production code unless a test cannot be written otherwise; if you must, say why.

## What to cover

- The main success path with realistic inputs
- Invalid input, missing files, and explicit error returns
- Boundary values that the implementation actually branches on

## Output

- Write the test file. Summarize what is covered in a few bullets.
- Do not invent a second test framework.
