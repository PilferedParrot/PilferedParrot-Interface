# Interface design language

Work and Chat share one set of components in `web_assets/app.css`. Themes supply
color and artwork; they do not change the placement or behavior of controls.

## Layout and information

- Use 8 px between related controls, 16 px between groups, and 24 px around desktop
  dialog content. Compact dialogs use 16 px. Separate interface sections with
  spacing, not horizontal rules. Borders identify fields or enclose surfaces;
  separators inside user-authored Markdown and tables retain their meaning.
- Use 10 px corners for controls and 14 px for major surfaces. Keep typography at
  the readable 16 px base, with weight and spacing establishing hierarchy.
- Keep the project, session title, connection, selected model and reasoning, and
  current task progress easy to find. Context use remains visible in the collapsed
  sidebar summary; detailed telemetry expands on request.
- Organize each sidebar into workspace actions, connection and usage, then session
  history. Keep refresh, provider status, update information, and context together.
  History headings stay in the same box as their independently scrolling entries.
  Preferences is a separate utility button at the bottom that opens a dialog and
  stays visible while sidebar content scrolls. Only context expands in place. Use
  the same neutral border, corner radius, typography, and keyboard focus for
  Preferences, New Session, Providers, Chat, and Whiteboard.
- Use the same message surfaces and composer controls in Work and Chat. Preserve
  their functional distinction: Work can act in the project; Chat is read-only.

## Dialogs and actions

Every app dialog has an accessible title, a `.dialog-heading`, and one
`.dialog-close` at the upper right. The heading stays visible while the dialog
scrolls. The close button and Escape dismiss without submitting. Native dialog
focus handling returns the user to the opening control when it remains available.

Primary actions use a filled button and a specific verb, such as **Use folder**,
**Run in terminal**, or **Post message**. Secondary actions use an outlined or
quiet button. Place form actions at the bottom, with Cancel before the primary
action. Close belongs in the heading, not among those actions. Place list-level
utilities such as Add provider and Refresh together above the list.

Controls have visible keyboard focus. Dialog buttons are at least 40 px high on
desktop and 44 px on narrow screens. Dialogs fit the viewport and allow vertical
scrolling; long paths, provider labels, and errors wrap without hiding controls.
Cancellation must work even when a required field is empty.

Wide Markdown tables and preformatted content offer an expand control at the upper
right only while they overflow their available width. Keep this control outside
the horizontal scroll area. The expanded dialog fills the viewport, follows the
panel palette, retains readable content formatting, and supports Close and Escape.
Work activity and response details use the same nested surface inside flat message
cards. Both modes keep the same message-header alignment.

## Shared blue surfaces

The `surfaces` layer in `web_assets/app.css` is the authority for the visual
components in both modes. The `base` layer retains established layout fallbacks.
Add shared rules to the surface layer instead of another Work-only or Chat-only
paint override. Both modes use a 286 px default sidebar, identical header/card
alignment, 16 px text, and the same 760 px drawer breakpoint. Functional text and
actions may differ; their component styling does not.

`appearance.js` derives one blue-tinted panel and foreground pair from the theme
canvas and section colors. Opposite light/dark section and canvas fills use the
canvas tone so translucent controls remain legible. Readable foregrounds target
6:1 against the base panel, with at least 4.5:1 verified on control surfaces.
Stronger readability adds contrast and text separation. Arbitrary artwork still
varies pixel by pixel; use the available surface/readability settings as needed.
Never replace the palette with black merely because Minimal is selected.

Balanced uses a deliberate opacity hierarchy: 64% header/composer, 52% sidebar
groups, 48% message cards, 46% controls, and 82% code surfaces. Selected history
rows use the same blue family with a brighter border and inset selection mark.
Minimal lowers structural opacity while retaining the palette; Maximal makes
panels opaque. Dialogs use the solid panel, and native select popup options use
the same palette. Model and reasoning selectors share one rule in both modes.
Details sit inside the message card with a quiet nested surface, not a separate
black bar. Message headers have the same flat structure and typography in Work
and Chat. Keep text above the composited glass surface.

The theme owns its original background/frame/toolbar/attribution images and their
alignment, tiling, and scale. The app owns a consistent surface hierarchy above
them. Platform-owned authentication pages and browser chrome retain platform UI.

## Shared appearance settings

The server-backed application setting is authoritative. Do not restore appearance
from browser-local storage: desktop Work and Chat can use independent profiles.
The default is Original / Balanced / Standard. Apply saved settings at startup,
propagate visible-window changes, and refresh when a window becomes visible again.
Send partial updates so independent choices cannot clobber each other. A delayed
read cannot overwrite a newer choice; failed saves must report the failure and
restore persisted state. Keep appearance endpoints capability- and Origin-gated;
Chat's appearance controls do not grant Work operations.

## Keep the ordinary task simple

The default Work flow is choose a project and model, describe the task, and send.
Do not require a second task form, estimated model costs, or manual routing to use
the application. See the [Harness review](harness-review.md) for the retired
planner and the provider-native execution that remains underneath Work.

When changing a component, inspect Work and Chat at desktop and narrow widths,
with default and imported light/dark themes. Check keyboard dismissal, focus,
scrolling, long content, and cancellation as well as the static appearance.
