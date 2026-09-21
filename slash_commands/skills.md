# /skills - List available skills and suggest relevant ones

List the skills available to this agent. Check the following locations:

- The bundled skill catalog under `/opt/hatch/skills/`
- Workspace skills under `~/workspace/skills/` (if present)
- Any skills registered in the running session

For each skill, give: **name**, one-line description, and when to use it.

Additional focus from the user: {args}

Then recommend the 2-3 skills most relevant to the current conversation and say
why. If no skills are relevant, say so plainly instead of forcing a match.
