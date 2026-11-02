# Asking in Cowork

Cowork asks through `AskUserQuestion`, so the analyst clicks an answer instead of typing one. The
words of every question come from `../../asking.md` and the skill that asks it.

- **Every menu is an `AskUserQuestion` call.** Call `AskUserQuestion` for every menu in the skills
  and shared references. Never render bracketed choices as plain assistant text; the user must
  receive clickable options.
- **The atomic first interaction.** When a route has both a welcome and an initial menu, send the
  complete welcome word for word through `SendUserMessage`, then immediately call `AskUserQuestion`
  with the matching menu in the same assistant turn. Cowork can collapse text written just before a
  tool call, so the analyst never reads it. Only a session without `SendUserMessage` writes the welcome
  as assistant text. The tool call does not replace the welcome. Never send the menu alone or render
  its bracketed choices as assistant text.
- **The Other box.** It always accepts a typed answer, and a bare company name typed there is a
  company name, not a command.
