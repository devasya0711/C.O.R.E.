# Design System & UX Specification: C.O.R.E.

## 1. Visual Direction & Design Principles

C.O.R.E. embodies an **Industrial Precision Dark-Mode First**
aesthetic inspired by Linear, Raycast, and Vercel. It avoids decorative
gradients, marketing fluff, or generic AI sparkles in favor of
high-density technical clarity.

1. **Topology First:** The interactive code graph is the primary
workspace—chrome and toolbars float unobtrusively with translucent glass
borders.

2. **Semantic Color Only:** Bright colors are reserved strictly for
graph semantics (Seam clusters, Blast Radius severity, CRAG pass/fail
states).

3. **Zero-Latency Feedback:** Every graph interaction, node
selection, and streaming agent transition responds in under 100ms.

---

## 2. Colors & Typography

### 2.1 Color Palette (Dark Mode Default)

- **Background Primary (Canvas):** `#090A0F` (Obsidian)

- **Background Elevated (Panels/Drawers):** `#11131C` (Carbon
Slate)

- **Surface Interactive (Cards/Inputs):** `#181B26`

- **Border Hairline:** `#262938` (`1px solid`)

- **Border Active/Focus:** `#4F46E5` (Indigo 600)

- **Text Primary:** `#F3F4F6` (Gray 100)

- **Text Secondary:** `#9CA3AF` (Gray 400)

- **Text Muted:** `#6B7280` (Gray 500)

### 2.2 Semantic & Graph Node Tokens

- **Node - Function:** `#3B82F6` (Blue 500)

- **Node - Class:** `#8B5CF6` (Violet 500)

- **Node - API Endpoint:** `#10B981` (Emerald 500)

- **Node - DB Table:** `#F59E0B` (Amber 500)

- **Blast Radius Path / Critical Risk:** `#EF4444` (Red 500 —
animated dashed stroke)

- **CRAG Verified / Test Pass:** `#22C55E` (Green 500)

### 2.3 Typography

- **UI Sans Font:** `Inter` or `Geist Sans` (`-0.015em`
letter-spacing on headings).

- **Code & Graph Node Font:** `JetBrains Mono` or `Geist Mono`
(used for all function signatures, Cypher badges, risk scores, and
terminal logs).

- **Type Scale:**

- `H1 (Page Title)`: `24px / 32px`, `font-semibold`

- `H2 (Section Header)`: `18px / 26px`, `font-semibold`

- `Body Default`: `14px / 20px`, `font-normal`

- `Mono Technical / Node Label`: `12px / 16px`, `font-medium`

---

## 3. Spacing & Layout System

- **Base Unit:** `4px` grid (`p-1` = 4px, `p-2` = 8px,
`p-4` = 16px, `p-6` = 24px).

- **App Shell:** Fixed left collapsible sidebar (`240px`
expanded, `64px` collapsed) + Top Command Header (`56px` height) +
Main Workspace (`calc(100vh - 56px)`).

- **Border Radius:** Crisp technical radius — `6px`
(`rounded-md`) for buttons and inputs, `8px` (`rounded-lg`) for
cards and modals.

---

## 4. Reusable Components

1. **Custom React Flow CodeNode:** Displays node type badge
(`FN`, `CLS`, `API`, `DB`), monospace symbol name, community
seam color ring, and incoming/outgoing edge count pills.

2. **Blast Radius Risk Gauge:** Circular or segmented horizontal
meter displaying score `0-100`, hop depth selector (`1` to `5`
hops), and affected endpoint list.

3. **Agent Execution Stepper:** Vertical timeline showing `Static
Analyzer` -> `Boundary Mapper` -> `Impact Predictor` -> `CRAG
Evaluator` -> `Refactor Coder` -> `Pytest Sandbox`, with
expandable monospace log drawers and retry badges.

4. **Diff & Artifact Viewer:** Tabbed Monaco/Shiki read-only code
viewer displaying generated microservice files, Dockerfile, and
Strangler Fig proxy config.

---

## 5. Navigation & Forms

- **Global Command Palette (`Cmd+K` / `Ctrl+K`):** Quick-jump
to any ingested repository, search for a function node by name across
the active graph, or trigger "Extract Seam".

- **Forms:** Inline validation with crisp `1px` borders; error
messages appear directly below inputs in `#EF4444` at `12px`.

---

## 6. Loading, Empty & Error States

- **Graph Canvas Loading:** Subtle radial dot-grid pulse with a
floating status pill (`"Computing Louvain community layout..."`).

- **Empty State:** Centered card on dark dot-grid canvas offering
two clear actions: `"Import GitHub Repo"` or `"Load Sample E-Commerce
Monolith"`.

- **Error State:** Non-destructive inline diagnostic card with
expandable stack trace / stderr drawer and `"Retry Step"` button.

---

## 7. Interactions & Animations

- **Blast Radius Propagation:** Selecting a node dims unrelated
nodes to `25%` opacity (`transition: opacity 150ms ease`) and
animates affected `CALLS` edges using a crimson marching-ants SVG
stroke (`stroke-dasharray: 6 4`).

- **Seam Cluster Hull:** Toggling "Show Microservice Seams" renders
subtle translucent background groupings around nodes sharing the same
`community_id`.

---

## 8. Responsive Behavior & Accessibility

- **Desktop-First Studio:** Optimized for `1280px+` viewports.
Below `1024px`, the Graph Inspector drawer collapses into a bottom
sheet.

- **Accessibility:** WCAG AA contrast (`4.5:1` minimum for text);
full keyboard navigation for the Command Palette (`Cmd+K`) and node
list fallback table; `prefers-reduced-motion` disables SVG edge
animations.

---

## 9. Page-Level Layout Guidance

- **`/dashboard`:** 3-column responsive grid of Repository Cards
showing total nodes, identified microservice seams, shared DB warnings,
and last ingestion timestamp.

- **`/repos/[repoId]/graph`:** 100% viewport React Flow canvas
(`bg-[#090A0F]` with `#1F2430` dot pattern). Floating top-left
toolbar for filter controls; floating right inspector panel (`380px`
wide) for Blast Radius simulation and Seam Extraction trigger.
