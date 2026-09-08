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
  sidebar summary; detailed telemetry and preferences expand on request.
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

## Themes

Use surface and foreground tokens together. Dialog fields, labels, secondary
buttons, notices, and status badges must inherit the panel foreground instead of
hard-coded pale text. Derive subtle borders from the panel foreground so imported
light, dark, and vivid themes retain visible controls. Keep the user's original
theme artwork and its positioning.

Platform-owned windows, authentication pages, browser permission prompts, and the
Chrome theme gallery retain their platform UI. The outer Windows caption remains
Chrome/Edge-owned; this release does not replace its window integration.

## Keep the ordinary task simple

The default Work flow is choose a project and model, describe the task, and send.
Do not require a second task form, estimated model costs, or manual routing to use
the application. See the [Harness review](harness-review.md) for the retired
planner and the provider-native execution that remains underneath Work.

When changing a component, inspect Work and Chat at desktop and narrow widths,
with default and imported light/dark themes. Check keyboard dismissal, focus,
scrolling, long content, and cancellation as well as the static appearance.
