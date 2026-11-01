# FL-MCP in CrownCode

CrownCode is the showroom (a Next.js site with a project catalog). FL-MCP stays in its own repository, with its own releases and CI. CrownCode shows it: a project page that explains it, gives the setup steps and links to the source. That is the same arrangement CrownCode already has for Kognita, with a page of its own because the setup needs explaining.

Do not move the server code into the CrownCode repository: it is released as a Python package, it has its own tests, and the licence service has to be deployed apart from a static site anyway.

## What was added to CrownCode (branch `claude/bold-franklin-u2nys9`)

| File | What |
|---|---|
| `platform/config/product-catalog.ts` | the `fl-mcp` entry: route `/fl-mcp`, icon, schema.org facts (SoftwareApplication, Windows and macOS, link to the repository), `updated` date |
| `platform/locales/en.json`, `tr.json` | `products.items.flMcp`: title, description, status, and the page text under `page` (facts, example requests, setup steps, limits, source) |
| `platform/pages/fl-mcp/index.tsx` | the page, built from `WorldPage` and the shared section classes, with copy buttons for the commands |
| `platform/styles/pages/fl-mcp.module.css` | the few rules the page needs (steps, code blocks, list of requests) |
| `platform/config/showroom-worlds.ts` | the project's colours and sector label for the atlas |
| `platform/public/og/fl-mcp.jpg` | the social card |
| `platform/scripts/generate-og-images.mjs` | the card's line; the script now also draws a single card and falls back to the generic planet |
| `docs/ADDING_A_PROJECT.md` | corrected: a new project needs a sector label |

The header menu, the "back to the atlas" badge, search, sitemap and structured data come from the catalog entry. Checked: type check, ESLint, the whole jest suite (including the project integrity and design rule tests), locale parity, and the page rendered in Turkish, English and at 320, 390 and 768 pixels wide without horizontal scrolling.

## Keeping the page true

CrownCode's `PRODUCT.md` says project status must stay truthful, so the page says **Experimental** and states that FL-MCP has not been run in a live FL Studio. When that changes:

1. Run FL-MCP in a real FL Studio and fix what differs.
2. In both locale files change `products.items.flMcp.status`, the Status fact and the last item under limits; set `updated` in the catalog entry to the date of the change.
3. Only then add a screenshot or a short recording of a real session.

## When it is sold

The page deliberately shows no prices: nothing is for sale yet, and a page must not offer what does not exist. Before selling:

- Add a section for plans on the page, with the checkout links of the payment provider (the provider hosts the checkout and the customer portal, so CrownCode needs no accounts or login).
- Update `terms.tsx` and `privacy.tsx`: the service stores e-mail, device names and a credit ledger; the payment provider is a processor; refunds; what happens to unused credits; what is not promised (FL Studio's scripting limits, no live FL test yet).
- Deploy the licence service (see COMMERCE.md) on an always-on host with a durable disk, under its own address such as `licence.<your domain>`. Do not use the free Hugging Face Space that runs the CrownCode backend: it sleeps and its disk is not durable.
- The browser never calls the licence service (the customer's computer does), so the page's Content-Security-Policy `connect-src` needs no change.
- Build the extension with that address: `python packaging/build_mcpb.py --service https://licence.<your domain>`.

## Optional

- Give the project its own planet: render it with `platform/scripts/capture-atlas-assets.py`, add `fl-mcp` to `RENDERED_WORLDS` in `config/showroom-worlds.ts`, redraw the card with `node scripts/generate-og-images.mjs fl-mcp`.
- Publish the package to PyPI so the setup becomes `uv tool install fl-mcp`, then change the command on the page.
