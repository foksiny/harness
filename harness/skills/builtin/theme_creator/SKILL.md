---
name: theme_creator
description: Enables Harness to create, customize, and manage custom color themes for the CLI interface. Trigger when user wants to create a theme, customize colors, or design a personal palette.
triggers: [create theme, new theme, custom theme, design theme, make theme, build theme, theme creator, customize colors, color palette, personal theme]
---
# Theme Creator Skill for Harness

Use this skill whenever the user asks to create a new custom theme, customize colors, design a color palette, or build a personal theme for the Harness CLI.

## Theme System Overview

Harness themes are defined by 11 color fields + a code syntax theme:

| Field | Purpose | Example |
|-------|---------|---------|
| `primary` | Main accent color (headers, highlights) | `#00ffff` |
| `secondary` | Secondary accent (sub-headers) | `#ff007f` |
| `accent` | Highlight color (links, special items) | `#ffff00` |
| `success` | Success messages, checkmarks | `#00ff66` |
| `warning` | Warnings, alerts | `#ffaa00` |
| `error` | Errors, failures | `#ff3366` |
| `muted` | Subdued text, timestamps, dimmed | `#6c7086` |
| `text` | Standard body text color | `#ffffff` |
| `border` | Panel borders, separators | `#00ffff` |
| `thinking` | Thought accordion, reasoning color | `#bd93f9` |
| `code_theme` | Pygments syntax highlighting theme | `monokai` |

## Built-in Themes (14 available)
- `cyberpunk`, `dracula`, `nord`, `monokai`, `catppuccin`, `matrix`, `minimal`, `tokyo_night`, `solarized_dark`, `gruvbox`, `synthwave`, `one_dark`, `amber_crt`, `rose_pine`

## Custom Theme Storage
Custom themes are saved to JSON files:
- **Global**: `~/.harness/themes.json` (available across all workspaces)
- **Workspace**: `.harness/themes.json` (project-specific, overrides global)

## Available Functions (from `harness.themes`)

```python
from harness.themes import (
    create_custom_theme,      # Create and save a theme
    delete_custom_theme,      # Delete a custom theme
    interactive_create_theme, # Interactive wizard
    validate_theme_dict,      # Validate theme data
    get_theme,                # Get theme by name
    list_themes,              # List all themes
    render_theme_preview,     # Visual preview card
    THEMES,                   # Dict of all loaded themes
)
```

## Step-by-Step Procedure for Creating a Custom Theme

### 1. Clarify Requirements
- Ask user for theme name (lowercase, underscores: `my_custom_theme`)
- Ask for display name (human-readable: `My Custom Theme`)
- Determine if global or workspace scope
- Ask if they want interactive wizard or provide all colors at once

### 2. Collect Color Palette
Colors can be:
- **Hex codes**: `#ff007f`, `#00ffff`, `#1a1b26`
- **Rich named colors**: `bold white`, `bright_cyan`, `dim cyan`, `green`, `red`
- **RGB**: `rgb(255,0,127)` (Rich supports this)

Required 10 colors: primary, secondary, accent, success, warning, error, muted, text, border, thinking
Optional: code_theme (Pygments theme name like `monokai`, `dracula`, `fruity`, `vim`, `material`, `one-dark`, `solarized-dark`, `gruvbox-dark`)

### 3. Create the Theme

**Option A: Direct creation (when user provides all colors)**
```python
from harness.themes import create_custom_theme

success, message = create_custom_theme(
    name="my_theme",
    display_name="My Theme",
    colors={
        "primary": "#00ffff",
        "secondary": "#ff007f",
        "accent": "#ffff00",
        "success": "#00ff66",
        "warning": "#ffaa00",
        "error": "#ff3366",
        "muted": "#6c7086",
        "text": "#ffffff",
        "border": "#00ffff",
        "thinking": "#bd93f9",
    },
    code_theme="monokai",
    global_scope=True  # False for workspace-only
)
```

**Option B: Interactive wizard (when user wants guided creation)**
```python
from harness.themes import interactive_create_theme

success, message = interactive_create_theme(global_scope=True)
```

### 4. Verify and Preview
After creation, show the theme preview:
```python
from harness.themes import render_theme_preview, get_theme

# Show preview
card = render_theme_preview("my_theme")
console.print(card)

# Or switch to it immediately
from harness.config import save_config
agent.config.theme = "my_theme"
save_config(agent.config)
renderer.set_theme("my_theme")
```

### 5. Handle Edge Cases
- **Name conflicts**: If name exists, ask to overwrite or choose different name
- **Built-in protection**: Cannot overwrite built-in themes (14 protected names)
- **Validation**: All 10 color fields required; code_theme optional (defaults to monokai)
- **Scope**: Global themes persist across projects; workspace themes are project-specific

## Example Interaction Flow

**User**: "Create a custom theme called 'ocean' with blue colors"

**Agent**:
1. Ask clarifying questions if needed
2. Create theme with ocean-inspired palette:
   ```python
   create_custom_theme(
       name="ocean",
       display_name="Ocean Deep",
       colors={
           "primary": "#0077ff",
           "secondary": "#00ffff",
           "accent": "#7fffd4",
           "success": "#00ff88",
           "warning": "#ffaa00",
           "error": "#ff4444",
           "muted": "#4488aa",
           "text": "#e0f0ff",
           "border": "#0077ff",
           "thinking": "#88ccff",
       },
       code_theme="dracula",
       global_scope=True
   )
   ```
3. Show preview with `render_theme_preview("ocean")`
4. Offer to switch to it

## Common Pygments Code Themes
`monokai`, `dracula`, `fruity`, `vim`, `material`, `one-dark`, `solarized-dark`, `gruvbox-dark`, `native`, `default`, `emacs`, `friendly`, `colorful`, `autumn`, `borland`, `bw`, `manni`, `murphy`, `pastie`, `perldoc`, `rrt`, `tango`, `trac`, `xcode`, `igor`, `paraiso-dark`, `paraiso-light`, `monokai`, `lovelace`, `algol`, `algol_nu`, `arduino`, `rainbow_dash`, `abap`, `vs`, `stata`, `stata-light`, `stata-dark`

## Deleting a Custom Theme
```python
from harness.themes import delete_custom_theme

# Searches both scopes automatically (global first, then workspace);
# the scope parameter only sets the search order.
success, message = delete_custom_theme("my_theme")
```

## Best Practices
- Suggest previewing before applying
- Recommend global scope for personal themes, workspace for team-shared themes
- Validate color contrast for readability
- Use descriptive display names
- Suggest popular code_theme pairings (e.g., dracula theme → dracula code_theme)

## Integration with /theme Command
Users can also use the CLI command directly:
- `/theme` - Theme gallery with previews
- `/theme list` - List all themes, marked (built-in) vs (custom)
- `/theme <name>` - Switch theme
- `/theme preview <name>` - Show preview card
- `/theme create` - Interactive creation wizard (prompts for name and the 10 colors)
- `/theme delete <name>` - Delete a custom theme (built-ins are protected)