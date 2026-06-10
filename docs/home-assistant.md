# Home Assistant

BitMon can control your smart home through **Home Assistant** — ask the pet to
turn lights and devices on or off, and it does. It talks to Home Assistant over
an **MCP** (Model Context Protocol) endpoint, and you choose exactly which
devices it's allowed to touch.

> [!NOTE]
> This integration is **optional**. If you don't configure it, the Home Assistant
> tool and the Devices tab simply don't appear.

---

## 1. Install the MCP server add-on

BitMon talks to Home Assistant through the
**[ha-mcp](https://github.com/homeassistant-ai/ha-mcp)** add-on
(Home Assistant MCP Server). Install it in your Home Assistant instance and copy
the private MCP URL it generates (it looks like
`http://homeassistant.local:9583/private_...`).

> [!IMPORTANT]
> Use **ha-mcp version 7.7 or newer**. BitMon resolves the add-on's tools
> dynamically, so it keeps working across add-on updates, but 7.7+ is the
> supported baseline.

---

## 2. Connect the MCP

In **MCPs → Home Assistant** (see [Configuration](configuration.md#mcps)):

1. Paste your **Home Assistant MCP URL**.
2. Tick **Enable Home Assistant**.
3. Click **Validate connection**. BitMon connects, refreshes its tool cache, and
   reports success or the error.


Once validated and enabled, a **Devices** tab appears and the pet gains a
home-control tool.

---

## 3. Pick the devices the pet may control

Open the **Devices** tab. By default the pet shouldn't see your whole house — you
curate the list here.


| Control | What it does |
|---|---|
| **Import from MCP** | Pulls the current entity list from Home Assistant into the table. |
| **Search** | Filter by name, entity id, domain, or alias. |
| **Select all** | Toggle every visible device at once. |
| **Save devices** | Persist your selection (this is required — it doesn't auto-save). |

For each device you can:

- **Enable** it (the *Use* checkbox) so the pet is allowed to control it.
- Add **aliases** — extra natural-language names the pet will recognise, e.g.
  "the big light", "sala", "tv da sala". Aliases dramatically improve how
  reliably voice commands map to the right device.

The selected devices are cached locally so the pet can resolve commands quickly.

---

## 4. Use it

Just ask, by voice or in the Conversations chat:

- *"Turn on the living room light."*
- *"Desliga a tv."*
- *"Set the bedroom lamp to 30%."*

The pet maps your phrasing to an enabled device (using names and aliases),
chooses the action, and calls Home Assistant. If it can't find a match it tells
you.

---

## Privacy note

Home Assistant data is local to your machine, but the **device names and your HA
URL** end up in BitMon's cache and logs. Review `cache/` and `logs/` before
sharing logs or publishing a fork. See [Providers → Privacy](providers.md#privacy).

---

## Other MCP servers

Home Assistant is just one MCP. Under **MCPs → External MCP servers** you can add
other **Streamable HTTP MCP** servers (some with OAuth) to give the pet more
tools. The mechanism is the same: configure the server, and its tools become
callable by the LLM.

---

Next: **[Troubleshooting](troubleshooting.md)**.
