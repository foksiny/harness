---
name: skill_creator
description: Enables Harness to autonomously author, structure, test, and register new custom agent skills on user request.
triggers: [create skill, new skill, add skill, author skill, generate skill, make a skill]
---
# Skill Creator for Harness

Use this skill whenever the user asks to create a new skill, generate a skill, or turn a workflow into a reusable agent skill.

## Skill Folder Structure
Every Harness skill lives in a directory under `~/.harness/skills/<skill_name>/` (global) or `.harness/skills/<skill_name>/` (workspace):

```
<skill_name>/
├── SKILL.md                 # Primary instruction file with YAML frontmatter
├── scripts/                 # Optional executable helper scripts (Python, Bash)
└── templates/               # Optional code templates, boilerplate, or examples
```

## Step-by-Step Procedure for Creating a Skill

1. **Clarify the Skill Purpose & Scope**:
   - Determine the skill name (lowercase, alphanumeric, underscores: e.g. `kubernetes_ops`, `nextjs_scaffold`).
   - Identify triggers: keywords that should automatically activate this skill.
   - Summarize core capabilities and constraints.

2. **Draft the Frontmatter**:
   ```yaml
   ---
   name: <skill_name>
   description: <Clear, concise description of what the skill does and when to invoke it>
   triggers: [<keyword1>, <keyword2>, <phrase>]
   ---
   ```

3. **Author High-Impact Instructions**:
   - Write comprehensive, step-by-step procedures.
   - Include edge cases, common pitfalls, and error mitigation steps.
   - Provide concrete examples of tool invocations or command sequences.

4. **Write the Files**:
   - Use `write_file` to write to `.harness/skills/<skill_name>/SKILL.md` (for project) or `~/.harness/skills/<skill_name>/SKILL.md` (for global).
   - If auxiliary scripts are needed, create them in `scripts/` and make them executable.

5. **Verify and Reload**:
   - Run the slash command `/skills` or inform the user that the skill has been successfully registered and is active immediately!
