# AeroPredict design

The site should feel like a well-made travel tool: calm, quick to read, and trustworthy. The reference
is airport signage and departure boards, not a tech demo.

## Principles

1. **One job per screen.** The homepage is for checking a flight. Everything else is one click away.
2. **Say it in numbers.** Lead with the figure that matters (the chance of arriving on time), then explain.
3. **Quiet surfaces, loud actions.** Backgrounds and cards stay flat and dark; the only bright colour is
   on the buttons and in the status colours.
4. **Honest by default.** Every estimate says what it's based on. No hype words, no badges claiming "AI".
5. **Fast.** No background animation, no blur, no heavy libraries on first load. Charts and maps load
   only when someone opens those sections.

## Colour

Dark, slightly blue ink with solid surfaces. Colours live as CSS variables in `frontend/style.css`.

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0C1117` | page background |
| `--surface` | `#131A22` | cards, inputs |
| `--surface-2` | `#19212B` | raised rows, hover |
| `--line` | `#26303C` | borders and dividers |
| `--text` | `#E9EDF2` | body text and headings |
| `--muted` | `#8B97A6` | secondary text |
| `--accent` | `#F2C230` | primary buttons, focus rings, links on hover (airport-sign yellow) |
| `--on-accent` | `#14110A` | text on yellow |
| `--good` | `#3DB07A` | on time, eligible |
| `--warn` | `#E8893A` | delayed, medium risk |
| `--bad` | `#E25555` | cancelled, high risk |
| `--info` | `#6FA8DC` | links in text, neutral highlights |

Rules: yellow is only for actions and focus. Green / orange / red are only for flight outcomes and risk.
Never put text on gradients.

## Type

- **IBM Plex Sans** 400 / 500 / 600 for everything.
- **IBM Plex Mono** 500 for flight numbers, airport codes, times and big figures, like a departure board.
- Scale (rem): 0.8 small · 0.9 secondary · 1 body · 1.25 card title · 1.75 section title · clamp(2.2–3.4) hero.
- Headings use weight 600 and slight negative tracking; body text never goes below 0.8rem.
- Labels are sentence case, not all caps (except the tiny mono field labels in the search bar).

## Spacing and shape

- 4px base grid: 4, 8, 12, 16, 24, 32, 48, 64, 96.
- Content width 1120px; reading width 720px.
- Radius 10px for cards, 8px for inputs and buttons, 999px only for small chips.
- Borders do the separating; shadows are reserved for the open mobile menu.

## Components

- **Buttons:** primary is yellow with dark text; secondary is outlined. Height 44px (48px in the search bar).
  Every primary button says exactly what happens: "Check my flight", "Check what I'm owed".
- **Inputs:** 44–48px tall, `--surface` background, 1px `--line` border, yellow focus ring.
- **Cards:** `--surface`, 1px border, 10px radius, 24px padding (16px on phones). No blur, no glow.
- **Section header:** left-aligned title plus one sentence. No eyebrow tags.
- **Icons:** 20–24px line icons (inline SVG, 1.75px stroke, `currentColor`). No emoji in the interface.
- **Result:** flight code and route in mono, one big on-time figure, one three-part bar
  (on time / delayed / cancelled), then the basis note, the rights box and the factors.

## Layout

- Phone first. Breakpoints at 640px, 900px and 1100px.
- Tool sections: form on the left, result on the right on wide screens; an empty state fills the right
  column until there's a result. On phones everything stacks.
- Navigation: wordmark left, links right, one "Check a flight" button. On phones a menu button opens a
  solid panel.

## Accessibility

- Text contrast at least 4.5:1 (muted text on `--bg` is 5.6:1).
- Visible focus on every control; status is never shown by colour alone (always a word or number too).
- Respect `prefers-reduced-motion`.
