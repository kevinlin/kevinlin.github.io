---
name: Kevin Lin — Professional Profile
description: A warm, structural personal-brand site for a principal software architect and people lead.
colors:
  architects-blue: "#0063a3"
  architects-blue-dark: "#4389b9"
  signal-coral: "#ff5a5f"
  signal-coral-light: "#ff8085"
  ink: "#333333"
  muted-ink: "#666666"
  surface: "#ffffff"
  section-mist: "#f8f9fa"
  border: "#e6e6e6"
  ink-on-dark: "#f8f9fa"
  surface-dark: "#121212"
  section-dark: "#1e1e1e"
  border-dark: "#3a3a3a"
typography:
  display:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "3.5rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Inter, sans-serif"
    fontSize: "2.5rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Inter, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 700
    lineHeight: 1.2
  body:
    fontFamily: "Inter, sans-serif"
    fontSize: "1.1rem"
    fontWeight: 400
    lineHeight: 1.7
  label:
    fontFamily: "Inter, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    letterSpacing: "0.5px"
rounded:
  sm: "4px"
  md: "8px"
  pill: "20px"
  capsule: "30px"
  full: "9999px"
spacing:
  xs: "8px"
  sm: "15px"
  md: "25px"
  lg: "40px"
  section: "100px"
components:
  button-primary:
    backgroundColor: "{colors.architects-blue}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "16px 32px"
  button-primary-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
  button-resume:
    backgroundColor: "{colors.architects-blue}"
    textColor: "{colors.surface}"
    rounded: "{rounded.capsule}"
    padding: "12px 24px"
  button-resume-hover:
    backgroundColor: "{colors.signal-coral}"
    textColor: "{colors.surface}"
  social-icon:
    backgroundColor: "rgba(0, 99, 163, 0.1)"
    textColor: "{colors.architects-blue}"
    rounded: "{rounded.full}"
    height: "50px"
    width: "50px"
  tech-badge:
    backgroundColor: "{colors.architects-blue}"
    textColor: "{colors.surface}"
    rounded: "{rounded.pill}"
    padding: "0.25rem 0.75rem"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "40px 30px"
---

# Design System: Kevin Lin — Professional Profile

## 1. Overview

**Creative North Star: "The Trusted Architect"**

This is the site of someone who designs systems for a living, so the site behaves like one: a clear grid, a load-bearing color, and structure you can read at a glance. Blue does the structural work; type does the talking; warmth comes from generous spacing and the way surfaces respond to a hand, not from decoration. The register is confident and senior with nothing to prove, and human enough that a recruiter feels talked to rather than sold at.

Restraint here is deliberate, not timid. Every section earns its place against one job: move the right visitor toward a conversation. Substance carries the page — real credentials, real work, real numbers — so effects stay quiet. The single warm accent (coral) is held in reserve for the moment of interaction, which keeps the resting state calm and makes a hover feel like a small reward.

This system explicitly rejects the interchangeable AI/template dev portfolio (gradient hero, identical icon-heading-text card grids, tracked uppercase eyebrows over every section), the flashy startup landing page (salesy tone, motion everywhere), the terminal/hacker costume (dark mono, matrix green), and the stiff corporate brochure with no personality. If a choice reads as any of those, it is wrong for this brand.

**Key Characteristics:**
- One load-bearing brand blue; one rare warm accent reserved for interaction.
- Single type family (Inter), hierarchy built from weight and size, not font-switching.
- Soft-lifted surfaces: flat-ish at rest, they rise gently toward the reader on hover.
- Light and dark themes as first-class equals, toggled by the reader.
- Structure over ornament: the design gets out of the way of the credentials.

## 2. Colors

A single dependable blue carries structure and action; everything else is a quiet neutral, with one warm accent held back for interaction.

### Primary
- **Architect's Blue** (#0063a3): The load-bearing color. Marks every link, action button, icon, section-heading accent, the h2 underline, and the brand wordmark. In dark theme it lightens to **Architect's Blue (Lifted)** (#4389b9) to hold contrast on near-black. This is the color the whole system leans on.

### Secondary
- **Signal Coral** (#ff5a5f): The one warm accent. Appears almost exclusively on interaction — link hover, the resume button on hover, project-title hover — never as a resting fill. Its rarity is the point. Lightens to (#ff8085) in dark theme.

### Neutral
- **Ink** (#333333): Primary body text and heading color in light theme; also the footer surface and the primary-button hover fill.
- **Muted Ink** (#666666): Secondary/supporting paragraph text. Passes ~5.6:1 on Section Mist; keep it for supporting copy, not fine print.
- **Surface** (#ffffff): Page background and the fill of cards that sit on tinted sections.
- **Section Mist** (#f8f9fa): The alternating section background that creates page rhythm.
- **Border** (#e6e6e6): Hairline borders and dividers between cards and sections.
- **Dark-theme neutrals**: Surface (#121212), Section (#1e1e1e), Border (#3a3a3a), text (#f8f9fa), muted (#cccccc).

### Named Rules
**The Blue-Carries-Structure Rule.** One brand blue does all the structural and interactive work — links, buttons, icons, accents, the heading underline. Do not introduce a second "primary." If something needs emphasis, use weight, size, or the blue you already have.

**The Coral-On-Contact Rule.** Signal Coral appears only in response to interaction, never at rest. A resting coral fill breaks the calm the system depends on.

## 3. Typography

**Display / Body / Label Font:** Inter (with `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto` fallback stack)

**Character:** One humanist-geometric sans doing every job. The voice comes from weight and size contrast, not from pairing — clean and current without tipping into either sterile or trendy. Base line-height is generous (1.7) so long professional copy stays readable.

### Hierarchy
- **Display** (700, 3.5rem, line-height 1.2, letter-spacing -0.02em): The profile name in the header. Steps down responsively to 2.2rem on small screens.
- **Headline** (700, 2.5rem, line-height 1.2): Section titles (`h2`), each carrying the blue underline accent. Steps to 1.8rem on mobile.
- **Title** (700, 1.75rem; category cards use 1.5rem): Subsection and card headings (`h3`), most set in Architect's Blue.
- **Body** (400, 1.0–1.2rem, line-height 1.7–1.8): Paragraph copy. Lead paragraphs run 1.2rem; keep measure near 65–75ch (About/intro copy caps around 800px).
- **Label** (500, 0.75rem, letter-spacing 0.5px, uppercase): Tech badges/chips only. The one place caps and tracking are allowed.

### Named Rules
**The One-Family Rule.** Inter carries the whole system. Hierarchy is built from weight (400/500/600/700) and size, never from adding a second display face. A single family chosen deliberately beats a timid pairing.

**The Underline Rule.** Every `h2` gets a 60px × 4px Architect's Blue bar beneath it (`h2::after`). It is the system's signature section marker — use it instead of an uppercase eyebrow above the heading.

## 4. Elevation

Soft-lifted, not flat and not heavy. Surfaces rest with a wide, low-opacity ambient shadow and rise toward the reader on hover. Depth is diffuse and gentle — the feeling of a well-lit desk, not a dropdown menu. Dark theme deepens the same shadows rather than removing them.

### Shadow Vocabulary
- **Ambient rest** (`box-shadow: 0 10px 30px rgba(0,0,0,0.05)`; dark: `0.2` alpha): Default card resting shadow (`--card-shadow`).
- **Lifted hover** (`box-shadow: 0 15px 40px rgba(0,0,0,0.1)` up to `0 20px 40px rgba(0,0,0,0.1)`): Cards on hover, paired with `translateY(-5px to -10px)`.
- **Brand glow** (`box-shadow: 0 5px 15px rgba(0,99,163,0.2)`): Primary/resume buttons — a blue-tinted shadow, not neutral, so buttons feel connected to the brand color.
- **Inset track** (`box-shadow: inset 0 1px 3px rgba(0,0,0,0.1)`): The recessed skill-bar track.

### Named Rules
**The Rise-On-Hover Rule.** Interactive surfaces (cards, buttons, social icons) lift with `translateY` and deepen their shadow on hover. Movement is up and toward the reader, easing out over ~0.3s. No bounce, no scale-only.

## 5. Components

### Buttons
- **Shape:** Rectangular with a small radius (`4px`) for the primary contact button; a full capsule (`30px`) for the resume button.
- **Primary (contact):** Architect's Blue fill, white text, padding `16px 32px`, brand-glow shadow. Hover fills to **Ink** (#333) and lifts `-5px`.
- **Resume:** Architect's Blue capsule, white text, padding `12px 24px`. Hover fills to **Signal Coral** and lifts `-3px` — one of the few sanctioned coral appearances.
- **Certification button:** Currently a warm orange→red gradient. Treat as legacy; see Don'ts. Prefer a solid Architect's Blue or Coral fill for new work.

### Chips / Badges
- **Tech badge:** Solid Architect's Blue pill (`20px`), white uppercase label, `0.75rem`, letter-spacing `0.5px`, padding `0.25rem 0.75rem`.
- **Tech-stack pill:** Tinted variant — `rgba(0,99,163,0.1)` background, blue text — for lighter emphasis.

### Cards / Containers
- **Corner style:** `8px` radius (`--rounded md`); highlight items use `6px`.
- **Background:** Surface white on tinted sections; flips to Section-dark in dark theme.
- **Shadow strategy:** Ambient rest → Lifted hover (see Elevation).
- **Border:** 1px Border hairline on most cards; the skill category adds a `4px` Architect's Blue **top** border as an accent (top-only — never a left/right side stripe).
- **Internal padding:** `40px 30px` on feature cards, `20px` on compact highlight items.

### Navigation
- **Style:** Fixed top bar, translucent (`rgba(255,255,255,0.95)`, dark: `rgba(18,18,18,0.95)`) with `backdrop-filter: blur(10px)` and a hairline bottom border.
- **Links:** Inter 500, Ink at rest; hover/active shift to Architect's Blue on a faint tinted background. Focus shows a 2px blue outline, offset 2px.
- **Mobile:** Hamburger toggles a full-width dropdown panel; lines animate into an X.

### Skill Progress Bars (legacy signature)
- Recessed track (Section Mist, inset shadow) with an animated fill (`width` transition 1.5s). Each category currently uses its own multi-hue gradient. Documented as-is; see Don'ts for the caution on decorative rainbows and invented percentages.

### Photo Slider (signature)
- Full-width `500px` slides (400/300px on smaller screens), horizontal `transform` transition. Captions sit in a bottom `linear-gradient(to top, rgba(0,0,0,0.8), transparent)` overlay with white text. Circular translucent prev/next controls and a dot indicator, both above the image on `z-index: 10`.

### Section Heading (signature)
- `h2` in Ink/light-on-dark with the **Underline Rule** blue bar beneath. This is the canonical section opener across the whole page.

## 6. Do's and Don'ts

### Do:
- **Do** route every action and link through **Architect's Blue** (#0063a3); let one color carry structure.
- **Do** reserve **Signal Coral** for interaction states only (hover), never a resting fill.
- **Do** build hierarchy from Inter weights (400/500/600/700) and size, keeping to the one family.
- **Do** open every section with an `h2` and its 60×4px blue underline instead of an uppercase eyebrow.
- **Do** keep surfaces soft-lifted: ambient shadow at rest, `translateY` up + deeper shadow on hover, easing out ~0.3s.
- **Do** use `repeat(auto-fit/auto-fill, minmax(280–350px, 1fr))` grids so layouts reflow without hand-tuned breakpoints.
- **Do** support both themes equally and keep body copy toward the Ink end for contrast.

### Don't:
- **Don't** let it read as a **generic AI/template dev portfolio** — no gradient hero, no identical icon-heading-text card grids repeated endlessly, no uppercase tracked eyebrow over every section.
- **Don't** make it a **flashy startup landing** (salesy tone, motion on everything) or a **terminal/hacker** costume (dark mono, neon green) or a **stiff corporate brochure** with no personality.
- **Don't** use a colored `border-left`/`border-right` greater than 1px as a side stripe on cards or callouts (the privacy-page `.contact-info` does this today — full border or top accent instead).
- **Don't** multiply decorative multi-hue gradients (the per-category skill bars and orange→red buttons drift toward templated rainbow and undercut "substance over polish"); prefer solid brand color.
- **Don't** state skill proficiency as invented percentages; numbers that read as made-up weaken a credibility-first page.
- **Don't** gate content visibility on a JS `.visible` class with `opacity:0` as the default — reveals never fire on reduced-motion or headless renders and the section ships blank. Animate from an already-visible state.
- **Don't** ship motion without a `@media (prefers-reduced-motion: reduce)` alternative (none exists in the current CSS — add one).
