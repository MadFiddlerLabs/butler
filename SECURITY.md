# Security

Butler handles a household's calendar, money and documents, so security reports get priority.

## Reporting a problem

Report vulnerabilities privately through GitHub: open the repository's **Security** tab and
choose **Report a vulnerability**. Please don't open a public issue for a security problem.

Include what you found, how to reproduce it, and what an attacker could do with it. Use
invented data in your report, never a real household's.

## What's in scope

- The reply trust check (`src/butler/inbox.py`) and the reply allowlist (`src/butler/replies.py`).
- The portal gate (`src/butler/portal.py`): who gets in, and which host names it answers to.
- Anything that sends personal data somewhere the README says it doesn't go.
- Prompt injection that turns news, calendar text or email into actions.

## Supported versions

Only the latest release gets fixes.
