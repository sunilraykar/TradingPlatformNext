# Repository Workflow Rules — TradingPlatformNext

These rules are mandatory for all AI assistants and contributors working in this repository.

## Main-only workflow
- Make all code and documentation changes directly on the existing `main` branch.
- Never create, switch to, or push a feature, fix, development, or temporary branch.
- Never create a pull request. Do not use a PR as a substitute for direct changes to `main`.
- Do not merge branches.
- Before editing, verify the repository is `sunilraykar/TradingPlatformNext`, the checked-out branch is `main`, and inspect `git status`. Preserve unrelated user changes.
- If the available environment cannot safely edit `main` directly, stop and explain the blocker. Do not silently fall back to another branch or PR.

## GitHub write authorization
- A request to implement a change authorizes work on `main` only within the scope of that request; follow any more restrictive explicit instruction from the user.
- Do not create PRs or branches unless the user explicitly authorizes that exception.
- Do not force-push, rewrite history, or discard user changes.
- Do not claim a change is on GitHub until the commit on `origin/main` has been verified.

## Verification and reporting
- Run relevant tests and checks where available.
- Review the diff for unintended changes.
- Report the exact files changed, commit SHA, tests actually run, and any checks that could not be run.
- Clearly distinguish local changes from changes pushed to GitHub.
