# Cinematic presentation

The frontend's presentation observes the existing public game state. The game rules,
agents, scoring, networking, keyboard controls, click-to-walk and data pipeline are unchanged.

- `web/src/characters.js`: articulated metallic characters with role-specific armour,
  helmets, tools and sensors, plus ORION's suit, shirt, lapels and tie. Static plates are
  merged by material within each animated joint to reduce draw calls.
- `web/src/environment.js`: surrounding data-center architecture, moving network packets,
  ambient particles and pooled bursts for patches, breaches, isolation and recovery.
- `web/src/scene.js`: detailed drive bays and rack hardware, reflection lighting, subtle
  bloom, scanning surfaces and translucent quarantine shields. The existing board positions,
  card states, fog of war, labels, score floats and click targets are retained.
- `web/src/presentation.js`: passive event announcements, a rendered 3D suited host,
  cached agent portraits, Web Audio effects and browser speech synthesis.
- `web/src/presentation.css`: responsive command-center layout, player advice, agent cards,
  briefing, transitions, bold results and reduced-motion styles.

The visual palette uses pale blue surfaces, navy text and distinct teal, gold and rose
status accents. Larger steel-blue racks and brighter character armour sit against a quiet
studio backdrop, with restrained bloom and no atmospheric fog over the board. The host
stays within the desktop sidebar so announcements do not cover either player's controls.
Popup headings, score details, vulnerability ranks, status chips and event messages have
explicit light-theme foregrounds. Contrast checks use a 4.5:1 threshold for DOM text;
pipeline animation changes the border glow while retaining its readable background.

## Audio

Audio unlocks after a click or key press. SFX and voice have independent switches near
Start/Reset; the play icon previews ORION. Preferences are remembered locally.
Speech uses an available English browser voice, preferring a male system voice at a low
pitch. Timbre depends on installed voices; captions work when speech is unavailable.
No speech service, account or API key is required by the new presentation.
Hidden attacker ledger rows never produce location-specific audio. Announcements use
public events and the existing match result, and never drive an agent or alter state.

## Verification

Run `npm run build` from `web/`. The built-in `?mock=1&autostart=0` mode lets a reviewer
start a normal-speed demo. Existing accelerated-demo parameters are useful for checking
result screens: `&tick=250&ticks=5&seed=12` produces a defender win, while
`&tick=250&ticks=8&seed=1` produces an attacker win with the current mock implementation.
These are UI scenarios, not measurements of live-game balance or model performance.

Browser checks cover both players patching, spoken announcements, sound controls, agent
portraits and both winner screens. No backend or pipeline files are modified.
