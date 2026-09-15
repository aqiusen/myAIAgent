---
name: code-review
description: Review source code for bugs, regressions, and design issues. Use when the user asks to review, audit, or critique code.
---

# Code Review

Review the code the user pointed to. Do not rewrite it unless they ask.

## How to work

1. Read the target files and nearby callers before commenting.
2. Report only issues that are real in this code. No generic checklists.
3. Order findings by severity: correctness, then regressions, then design.
4. Each finding: location, what is wrong, why it matters, a concrete fix.

## What to look for

- Broken control flow, missed error paths, off-by-one, race conditions
- Behavior changes that callers will notice
- Secrets, injection, path traversal, unsanitized shell
- Abstractions that hide the actual data flow

## Output

- Findings first. If none, say so in one sentence.
- Do not append a rewritten version of the file unless asked.
