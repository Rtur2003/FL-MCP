# Research notes

What was studied before and during the build, what was learned, and which decision each finding changed. Sources are linked; claims about other projects are limited to what their own READMEs and docs state.

## 1. How other DAW and editor MCPs are built

| Project | Bridge to the app | Notes |
| --- | --- | --- |
| [karl-andres/fl-studio-mcp](https://github.com/karl-andres/fl-studio-mcp) | JSON files plus a MIDI trigger note; a controller script in FL reads the file and answers through another file | Piano roll notes: the server writes a JSON request and presses `Ctrl+Alt+Y` / `Cmd+Opt+Y` (FL's "run last script") after bringing FL forward. Lists "cannot load plugins" and "no pattern management" as limits. ~60 one-purpose tools |
| [rosasynthesiz/flstudio-mcp](https://github.com/rosasynthesiz/flstudio-mcp) | Controller script on virtual MIDI ports | Note writing needs a piano roll script run once per session ("arms the note bridge"); troubleshooting table keyed on FL's script output |
| [MadBlast0/Fl-Studio-MCP](https://github.com/MadBlast0/Fl-Studio-MCP), [ohhalim/flstudio-mcp](https://glama.ai/mcp/servers/ohhalim/flstudio-mcp) | Installer copies the controller and piano roll scripts; `mido` talks to a virtual port | Same two-script split |
| [ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp) | A Remote Script inside Live opens a TCP socket on localhost:9877; the MCP server sends JSON | Live has a socket-friendly Python host, FL does not, so FL needs MIDI as the transport |
| [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp), [lordhoell/davinci-resolve-mcp](https://github.com/lordhoell/davinci-resolve-mcp) | Resolve's scripting API through `fusionscript` | One exposes the whole API as 440+ tools; the other adds an "advanced server" that does what the sanctioned API cannot |

What this changed here:

- **Transport.** FL's embedded Python cannot open sockets reliably and the API is only reachable from a controller script, so the link is MIDI. Frames are 7-bit CC messages with a checksum and retries, so a lost message is a retry, not a silent failure (`docs/ARCHITECTURE.md`).
- **Few tools, big verbs.** 60 or 440 tools cost thousands of tokens on every turn and models pick badly among them. The server has 14 always-on tools (about 4k tokens, checked by a test) with flat, null-free schemas; the long tail is reached through `fl_call` and `fl_api`.
- **Every listed limit became a rung of a ladder** instead of a stop sign (`docs/CAPABILITIES.md`): the API, FL's own UI driven from inside FL (`ui.navigateBrowser` and friends), a generated `.flp`, FL's command-line render, and, opt-in, the screen and keyboard. The Resolve "advanced server" is the same idea, and the FL projects above all end at the same two walls: loading plugins and writing notes.
- **Piano roll delivery.** Reusing FL's own "run last script" shortcut (as karl-andres does) is the only route found that needs no manual step after the first run; `fl_desk macro=script` presses it and checks `state.json` for the serial it queued, so success is verified, not assumed.
- **Safety.** Writes are small and put back by `fl-mcp selftest`; destructive keys are blocked in the desktop tier; FL is brought to the front before any input and input is refused otherwise.

## 2. Paid access: how MCP servers charge

Sources: [UsageBox, "MCP Server Billing 2026"](https://usagebox.com/articles/how-to-charge-for-mcp-server-2026-per-call-subscription-x402), [Zuplo, "4 MCP monetization models"](https://zuplo.com/blog/mcp-monetization-models), [Nevermined](https://nevermined.ai/blog/mcp-monetization-ai-agents), [Moesif](https://www.moesif.com/blog/api-strategy/model-context-protocol/Monetizing-MCP-Model-Context-Protocol-Servers-With-Moesif/), [Polar](https://polar.sh/), [Stripe credit grants](https://docs.stripe.com/api/billing/credit-grant/create) and [credits for usage-based billing](https://stripe.com/blog/introducing-credits-for-usage-based-billing).

Findings:

- Four models recur: per call, subscription, freemium, outcome based. Per-call only pays when a call is expensive (real compute, a paid upstream, a model); cheap lookups cost more to meter than they earn. Prepaid packs are the cleanest packaging because they cap exposure and prepay revenue.
- x402 turns a tool response into an HTTP 402 with a payment URI; the agent pays (USDC on Base) and retries with a receipt. Right for anonymous agent-to-agent calls, wrong for a person who owns a copy of a desktop tool.
- Agents and networks retry. Billing a retry is the fastest way to a dispute, so metering must be idempotent and must dedupe late and duplicate events.
- Stripe's credit grants have a **priority**, an optional **expiry** and a **scope** of what they may pay for, and are applied in priority order; Polar combines a base plan, prepaid credits and metered usage on one product and issues license keys with activation limits.
- Moesif and Zuplo advise per-tool meters, so each tool carries its own price.

Decisions (`docs/COMMERCE.md`):

- **Local first, credits only for finished work.** Driving FL, reading, plugin control, measuring and the desktop tier are free and offline. Only a new song plan, a `.flp` export and automatic fader balancing are metered (a small service holds the ledger).
- **A ledger, not a counter.** Append-only entries; the balance is derived. Credit **lots** have a priority and an expiry and are drawn down in order (a monthly allowance that expires before a top-up pack that does not), as in Stripe's model. Held credit is part of the ledger balance but not of the spendable balance.
- **Reserve, deliver, settle.** A call first reserves credits under an idempotency key, runs, and settles only the operations that were delivered; a hold nobody settles returns by itself, and a retried request replays instead of charging twice. Concurrent reservations never overspend (tested in parallel).
- **Licence key, then device token.** The key is typed once; `activate` signs a computer in under a seat limit (`--replace` frees one) and returns a device token. Webhooks from the payment provider grant lots idempotently per event and billing period. Tools that cost nothing never contact the service, so an outage cannot stop a producer who is only driving FL.
- **Not built, deliberately:** x402 and agent wallets (wrong buyer), per-call micro-metering of cheap tools, a hosted audio path.

## 3. FL Studio's command-line render

From Image-Line's manual (quoted in [their forum thread on the page's errors](https://forum.image-line.com/viewtopic.php?t=171471)), a batch AutoHotkey script ([JoshPennPierson/Batch-FLP-to-MP3](https://github.com/JoshPennPierson/Batch-FLP-to-MP3)) and the [change log](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/WhatsNew.htm):

- `FL64.exe /R /E<format> /F"<folder>" /O"<folder>"`: `/R` renders audio, `/E` takes one or more formats (`/Emp3,ogg,wav`), `/F` is a **source folder**: every `.flp` in it is rendered, `/O` (FL 20.1+) is the output folder, `/B` also takes the Backup folder, which is skipped otherwise. `/M` renders MIDI instead.
- FL starts its own copy, loads each project, renders and exits; the AutoHotkey script warns to close FL first and waits for the "Rendering to" window.

What this changed: the first version passed the project as a bare argument. `render.py` now follows the documented form: it renders the project's own folder when the project is alone there, otherwise a copy of it in a folder of its own, with `/O` pointing at a fresh output folder. The server refuses to start a render while FL answers on the MIDI link, because a running FL is the one case the batch renderer is known not to like; `fl_desk` exports the open project instead.

## 4. Loudness and mixing references

- ITU-R BS.1770-4 K-weighting and gating (absolute -70 LUFS, relative -10 LU) and EBU Tech 3342 loudness range (relative gate -20 LU, 10th-95th percentile of short-term values) are implemented in `audio.py` and checked against the standard's own 48 kHz filter coefficients and against test tones of known loudness. True peak is measured on a 4x oversampled signal.
- Streaming services turn music to roughly -14 LUFS; genre targets in `music/genres.py` are starting points for what that genre usually releases at, not rules.
- Role levels, EQ and compression starting points in `data/plugins.json` and the mix guide are the settings experienced mixers reach for first; the ear decides after that.

## 5. What could not be verified here

No copy of FL Studio ran during development. The controller script is tested against the official API stubs (an AST contract test) and a faithful fake; generated `.flp` files were checked against a real file written by FL Studio 20.8.4 and against PyFLP as an independent parser. Not yet seen on a real machine, and what `fl-mcp selftest` is for: the FL browser navigation quirks, plugins that do not report parameter text, preset names, cloned sampler channels being accepted by FL, the batch renderer on a given FL version, the export dialogs' timing, and the Windows and macOS desktop backends. The licence service was tested against its own ledger and webhooks, not against a live payment provider.
