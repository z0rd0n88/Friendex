---
name: rename-session
description: Generates a short, descriptive name for the current session. Use when user says "rename this session", "name this session", "/rename", or wants a session title.
---

# rename-session

Analyzes the current conversation and proposes a short, accurate name for it. The user applies it by clicking the session title in the Claude Code sidebar (or pressing the pencil icon) and pasting the suggested name.

## How to generate the name

Read the conversation so far — the full arc of what was asked and what was done. Then distill it into a name with these properties:

**Format**: 3–5 words, Title Case. No verbs if you can avoid them (noun phrases read better as titles). No filler words ("Session About", "Working On", "Help With").

**Content**: captures the *what*, not the *how*. "BratBot Age Gate Fix" beats "Debugging Discord Interaction Errors". The name should make sense a week from now when the user is scanning their session list.

**Specificity over generality**: "KitsuneBot Prompt Encryption" beats "Bot Prompt Work". If there's a specific file, feature, or bug at the center of the session, name it.

**Avoid**: vague nouns ("Misc Fixes", "Code Changes", "Various Updates"), timestamps, and anything that would look identical to another session.

## Output format

Present the suggested name on its own prominent line, like this:

```
Suggested session name:

  **BratBot Age Gate Fix**

To apply: click the session title in the Claude Code sidebar → edit → paste.
```

If the conversation covered multiple distinct topics, offer 2–3 alternatives ranked by how well they describe the *main* thing that happened:

```
Suggested session names (pick one):

  1. **KitsuneBot Hybrid Persona Setup** — primary focus
  2. **Supervisord Persona Wiring** — if infrastructure was the core work
  3. **BotHaus Persona Extension** — broader framing

To apply: click the session title in the Claude Code sidebar → edit → paste.
```

## Edge cases

- **Very early in the session** (1–2 exchanges): say so and suggest checking back after more work has been done. Don't invent a name from almost nothing.
- **Session is purely exploratory Q&A** with no concrete output: name the question, e.g. "Ollama Model Swap Options" or "ConversationHistory Redis Key Design".
- **Multiple unrelated topics**: name the one that took the most back-and-forth, and mention in a parenthetical that the session also covered X.
- **The user suggests their own name and wants a refinement**: improve their phrasing using the format rules above.
