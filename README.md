# KDE Connect MCP Server

A Model Context Protocol (MCP) server built with FastMCP that provides seamless integration with KDE Connect, allowing AI assistants to control your Android/iOS devices.

> **This is a fork** of [g20271/kdeconnect-mcp-server](https://github.com/g20271/kdeconnect-mcp-server)
> that adds **SMS support** (5 tools) on top of the upstream feature set.
> See [SMS / Conversations](#-sms--conversations) below, and
> [Licensing status](#-license) before reusing this code.

## ✨ Features

### Device Management
- 📱 **Device Discovery**: List all paired and reachable devices
- 🔋 **Battery Monitoring**: Check battery level and charging status
- 🔔 **Notifications**: View and send notifications

### Media Control
- 🎵 **Playback Control**: Play, pause, skip tracks
- 📻 **Player Management**: Switch between media players (YouTube, Spotify, etc.)
- 🔍 **Auto-Detection**: Automatically detect active media players
- 📊 **Now Playing**: Get current track information

### File & Content Sharing
- 📤 **File Transfer**: Send files to devices
- 🔗 **URL Sharing**: Share links that open in device browser
- 📥 **Received Files**: List and open files received from devices

### Device Location
- 🔊 **Ring Device**: Make device ring at maximum volume to locate it

### SMS / Conversations *(added in this fork)*
- 💬 **Read Threads**: List conversation threads and read full message history
- 🔎 **Search**: Diacritic- and case-insensitive search, optionally across every
  message body, not just the newest message of each thread
- 📊 **Honest Coverage**: Every result says how much of the mailbox was actually
  searched, so an empty result is never mistaken for "no such message exists"
- ✉️ **Send SMS**: Two-phase — a dry-run preview by default, dispatch only on
  explicit confirmation

## 🚀 Quick Start

### Prerequisites

- **Linux** with KDE Connect installed
- **Python 3.12+**
- At least one **paired KDE Connect device**

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/braklo/kdeconnect-mcp-server.git
   cd kdeconnect-mcp-server
   ```

2. Install the D-Bus bindings as **system packages** — `dbus-python` and
   `PyGObject` need the C libraries and headers of your distribution, and the
   wheels on PyPI do not build everywhere:
   ```bash
   sudo apt install python3-dbus python3-gi kdeconnect      # Debian / Ubuntu
   sudo zypper install python3-dbus-python python3-gobject kdeconnect-kde   # openSUSE
   ```

3. Create a virtual environment **that can see those system packages**, and
   install the rest into it:
   ```bash
   python3 -m venv --system-site-packages venv
   source venv/bin/activate
   pip install fastmcp
   ```

4. Verify KDE Connect is running:
   ```bash
   kdeconnect-cli --list-available
   ```

### Configuration for Claude Desktop

Add to your `~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kdeconnect": {
      "command": "/path/to/kdeconnect-mcp-server/venv/bin/python3",
      "args": [
        "/path/to/kdeconnect-mcp-server/mcp_server.py"
      ]
    }
  }
}
```

Replace `/path/to/` with the actual path to your installation.

## 🛠️ Available Tools (19)

### Device Management
1. **`list_devices`** - List all paired and reachable devices
2. **`get_battery`** - Get battery status (level, charging)
3. **`get_notifications`** - Get all active notifications
4. **`ring_device`** - Make device ring to locate it

### Media Control
5. **`get_now_playing`** - Get current media information
6. **`media_control`** - Control playback (Play, Pause, Next, Previous, Stop)
7. **`get_media_players`** - List available media players
8. **`set_media_player`** - Switch active media player
9. **`detect_active_player`** - Auto-detect which player is playing

### File & Content Sharing
10. **`share_file`** - Send file to device
11. **`share_url`** - Send URL to device
12. **`list_received_files`** - List files received from device
13. **`open_file`** - Open received file

### Notifications
14. **`send_notification`** - Send notification to device

### SMS / Conversations *(added in this fork)*
15. **`list_sms_conversations`** - List conversation threads (newest message per thread)
16. **`sms_cache_status`** - Cheap probe: how much history has loaded so far
17. **`read_sms_conversation`** - Read one thread's message history
18. **`search_sms`** - Search threads, optionally across full message bodies
19. **`send_sms`** - Send an SMS (two-phase, requires `confirm=True`)

## 💬 SMS / Conversations

### How loading works

KDE Connect does **not** hand you the mailbox in one call. The phone streams
thread heads to the daemon at roughly 4–5 per second, so a few thousand threads
take minutes to arrive, and message bodies accumulate the same way: repeated
`requestConversation` calls on one thread return progressively more until they
converge (measured 25 → 49 → 97 → 164, then stable). The `end` argument is not a
limit — convergence is.

Two consequences shape this API:

- **`conversationCreated` must not be used as a data source.** The daemon emits
  it only for a thread it does not already hold, so collecting those signals and
  returning them yields a shrinking, arbitrary subset on every repeat call while
  the complete data sits unused in `activeConversations()`. This server reads the
  cumulative cache instead.
- **Partial results are flagged, never hidden.** `list_sms_conversations` and
  `search_sms` report how much they actually covered.

### When is the thread list complete?

Cache size alone cannot tell you: before the stream starts, and after a couple
of unrelated pushes, the cache sits still exactly like a finished load
(measured: "complete" after 32 s with 90 of 1754 threads). So with
`refresh=true` the server asks the phone to re-stream and watches the daemon's
`conversationLoaded` signal, which fires once per thread head the phone delivers
— also for heads the daemon already holds. `complete` is `true` only when the
phone re-reported at least 98 % of the cached threads in *this* stream and then
went quiet for `quiet_seconds`; otherwise it is `false` after `wait_seconds`.

A full stream of ~1750 threads takes about five minutes, so a finished one is
remembered in `~/.cache/kdeconnect-mcp/full_stream.json` (per device: the
daemon's D-Bus owner and the head count). While the same daemon is running and
its cache has not shrunk, later calls — from any process — answer
`complete: true` with `complete_basis: "earlier_full_stream"` without waiting.
`load` in the result carries `streamed_threads`, `complete_basis` and, if the
cache emptied mid-call, `cache_reset`.

**The daemon's cache is emptied whenever the phone disconnects, even for a few
seconds** — the SMS plugin is recreated on reconnect. Afterwards a
`requestConversation` for a thread the daemon no longer holds is only logged
("Got a conversationID for a conversation with no messages!") and never
forwarded to the phone. `read_sms_conversation` detects this and returns
`not_in_daemon_cache: true` with a warning: an empty result there means an
unsearched thread, not an empty one. Re-list with `refresh=true` first.

### Searching

`search_sms` matches diacritic- and case-insensitively on both sides, so
`objednávka` finds `objednavka` — which matters, because carrier and shop
notifications are usually sent without diacritics.

By default only the newest message of each thread is searched (fast). Passing
`since="2026-08-01"` (or `deep=true`) turns on full-body search. Candidate
threads are then chosen exactly rather than heuristically: a thread head *is*
that thread's newest message, so a head older than `since` proves the thread
holds nothing in the window and is skipped without being fetched.

Two independent brakes keep an accidental multi-minute run out of reach:
`max_deep_threads` (50) and `deep_budget_seconds` (60). Anything that does not
fit is enumerated in the result, never silently dropped.

### Reading the result

Check `coverage.verdict`, not just `count`:

| verdict | meaning |
|---|---|
| `heads_only` | only the newest message of each thread was searched |
| `partial` | something was cut short — timeout, budget, cap, or heads still streaming |
| `complete` | everything in scope was searched |

When a search returns nothing and the verdict is not `complete`, the result also
carries `zero_matches_meaning`. **Treat that as an unsearched surface, not as
evidence that no such message exists.**

### Caveats

- MMS bodies are often empty (the content is an attachment), so full-body search
  will miss them.
- The daemon's cache is in memory: restarting `kdeconnectd` empties it.
- Contact names appear in `addresses` only when KDE Connect has the Contacts
  permission; otherwise you get phone numbers.

### Two daemon bugs this client works around

Both live in `kdeconnectd`, not here. Neither can be fixed from the client side;
what the client can do is avoid provoking them and refuse to misreport them.

**The daemon can deadlock in its SMS plugin** ([KDE bug 525025]). It keeps its
PID, keeps its name on the bus and simply stops answering, at 0% CPU, with the
main thread blocked taking a write lock in `Device::privateReceivedPacket` while
its own worker threads hold the read lock and wait on a condition. Observed three
times, always right after a deep (`since=`) search. Measured 2026-08-30: six
concurrent `requestConversation` calls over 200 threads froze it, while two
concurrent calls over the same 200 threads survived two consecutive runs, so
`SMS_DEEP_CONCURRENCY` defaults to 2. That is evidence rather than proof — the
underlying fault is a race — which is why raising it is a correctness risk and
not a speed knob.

Because a frozen daemon is otherwise indistinguishable from an empty mailbox,
every SMS read tool probes liveness after the heavy phase and reports
`status: "error"` with `coverage.verdict: "daemon_frozen"` rather than returning
zero results. Recovery is to kill the process — it respawns on the next D-Bus
call — and the SMS cache then starts from empty. You can exercise the probe
without provoking the bug: `kill -STOP` on the daemon looks the same over D-Bus,
and `kill -CONT` undoes it.

**The daemon crashes when too many requests are in flight at once**
([KDE bug 525021]): each pending `requestConversation` costs one eventfd, and
past roughly 990 the GLib allocation fails with `g_error`, which is a SIGTRAP and
a core dump rather than an error return. This client never approaches that — the
sliding window caps requests in flight, and streaming thread heads costs no
descriptors at all — but any client that fires requests without awaiting their
replies will hit it.

[KDE bug 525021]: https://bugs.kde.org/show_bug.cgi?id=525021
[KDE bug 525025]: https://bugs.kde.org/show_bug.cgi?id=525025

### Sending

`send_sms` is two-phase: without `confirm=true` it returns a dry-run preview
and sends nothing. Address the message with `phone`, not `thread_id`:
replying into an existing thread goes through `replyToConversation`, and on the
devices tested that produced a **group MMS** that included the sender's own
number and landed in a new thread. `phone` goes through
`sendWithoutConversation`, where each address must be marshalled as the D-Bus
struct `(s)` — a dict demarshals to an empty address and the phone drops the
packet without any error (that silently lost messages for weeks; there is a
regression test for it now).

Both D-Bus calls are fire-and-forget, so `send_sms` does not report "sent".
It watches the daemon's cache for the outgoing message (default 90 s; the phone
may take ~30 s to actually send) and returns one of:

| status | meaning |
|---|---|
| `confirmed` | the phone reported the message as sent (`thread_id_seen`, `date`) |
| `failed` | the phone marked it as failed |
| `unverified` | not seen within `verify_seconds` — may still be on its way, or dropped; ask the user to check the phone before treating it as sent |

### Required permissions

The KDE Connect app on the phone needs **Read SMS** and, for `send_sms`, **Send
SMS**. Without them the conversations plugin returns nothing at all.

### Concurrent clients

Every MCP client process talks to the same daemon. `list_sms_conversations` and
`search_sms` with `refresh=true` each ask the phone to re-stream all thread
heads, which disturbs any other client that is measuring or waiting on the
stream. Pass `refresh=false` when another process is working the same phone.

## 💬 Usage Examples

Ask Claude:

- "Show me all my KDE Connect devices"
- "What's the battery level on my phone?"
- "Pause the music on my phone"
- "Send this file to my tablet"
- "Make my phone ring, I can't find it"
- "What's currently playing on my phone?"
- "Find the SMS about my parcel delivery"
- "Search my messages for anything about an order since August 1st"
- "Read my conversation with +421900123456"

## 🏗️ Technical Architecture

### Framework
- **FastMCP 2.12+**: High-level Python framework for MCP servers
- **Pydantic**: Input validation with detailed schemas
- **D-Bus**: Direct integration with KDE Connect daemon

### Key Components

```
mcp_server.py (~1950 lines)
├── helpers: _parse_sms_message, _norm, _parse_since
├── KDEConnectDBus (~1000 lines)
│   ├── device / media / share / notification D-Bus wrappers (upstream)
│   └── SMS layer: thread streaming, deep loads, liveness probe, send + read-back
└── Tool definitions
    └── 19 tools with @mcp.tool() decorators (+ resources and prompts)
tests/test_sms_unit.py — unit tests that need no phone and no daemon
```

Tunables live as class constants on `KDEConnectDBus`: `SMS_DEEP_CONCURRENCY`
(2, see the deadlock note above), `SMS_STREAM_COVERAGE` (0.98),
`SMS_POLL_SECONDS` (2), `SMS_HEALTH_TIMEOUT` (10).

### Input Validation

Each tool uses Pydantic models with:
- Type checking
- Field descriptions
- Value constraints (min/max, regex patterns)
- Literal types for enums

Example:
```python
class MediaControlInput(BaseModel):
    device_id: str = Field(
        ...,
        description="The unique identifier of the KDE Connect device"
    )
    action: Literal["Play", "Pause", "PlayPause", "Next", "Previous", "Stop"] = Field(
        ...,
        description="Media control action to perform"
    )
```

## 🔧 Development

### Project Structure

```
kdeconnect-mcp-server/
├── mcp_server.py         # Main server implementation
├── requirements.txt      # Python dependencies
├── pyproject.toml       # Package configuration
├── README.md            # This file
├── SETUP.md             # Detailed setup guide (upstream, Japanese)
├── tests/               # Unit tests (no phone, no daemon needed)
└── test_mcp.py          # Upstream's manual protocol smoke test
```

### Tests

The unit tests stub the session bus and exercise the parts that have actually
broken before: address marshalling for `sendWithoutConversation`, the
completeness criterion, diacritic-insensitive matching, `since` parsing, thread
matching by phone number and the on-disk full-stream marker. They need the
`dbus` and `gi` packages importable, nothing else:

```bash
source venv/bin/activate
python -m unittest discover -s tests -v
```

Anything that touches the phone (streaming, deep search, sending) is tested
live only, with a real device and the user's explicit go-ahead for every
message sent.

### Extending the Server

1. Define a Pydantic model for input validation
2. Create a tool function with `@mcp.tool()` decorator
3. Add comprehensive docstring
4. Implement the logic using `KDEConnectDBus`

Example:
```python
class NewToolInput(BaseModel):
    device_id: str = Field(..., description="Device ID")

@mcp.tool()
def new_tool(input: NewToolInput) -> Dict[str, Any]:
    """
    Brief description

    Detailed explanation of what this tool does.

    Args:
        input: Tool input parameters

    Returns:
        Description of return value
    """
    return kdeconnect.some_method(input.device_id)
```

## 🐛 Troubleshooting

### Server doesn't start
```bash
# Check Python version (requires 3.12+)
python3 --version

# Verify FastMCP installation
source venv/bin/activate
python3 -c "from fastmcp import FastMCP; print('OK')"

# Check system dbus-python
python3 -c "import dbus; print('OK')"
```

### No devices found
```bash
# Verify KDE Connect daemon is running
ps aux | grep kdeconnect

# Check device pairing
kdeconnect-cli --list-available

# Restart daemon if needed
killall kdeconnectd
/usr/lib/x86_64-linux-gnu/libexec/kdeconnectd &
```

### D-Bus errors
```bash
# Install required system packages
sudo apt install python3-dbus python3-gi kdeconnect                      # Debian / Ubuntu
sudo zypper install python3-dbus-python python3-gobject kdeconnect-kde   # openSUSE
# and make sure the venv was created with --system-site-packages
```

### SMS tools return nothing, or `daemon_frozen`
See [Two daemon bugs this client works around](#two-daemon-bugs-this-client-works-around).
A frozen daemon is recovered with `kill <pid of kdeconnectd>`; it respawns on
the next D-Bus call with an empty SMS cache.

## 📋 Requirements

### System Requirements
- Linux with a session D-Bus (tested on Ubuntu 24.04 and openSUSE Tumbleweed)
- KDE Connect 24.x+ (SMS layer tested against 26.04 and 26.08)
- An Android phone running the KDE Connect app (iOS has no SMS plugin)

### Python Requirements
- Python 3.12+
- fastmcp >= 1.0.0
- dbus-python >= 1.2.0 and PyGObject >= 3.40 (system packages)

## 🤝 Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## 📄 License

⚠️ **Licensing status is unresolved.** Upstream
[g20271/kdeconnect-mcp-server](https://github.com/g20271/kdeconnect-mcp-server)
declares `license = {text = "MIT"}` in `pyproject.toml` but ships **no `LICENSE`
file**, so the default "all rights reserved" applies to the upstream portion of
this code. A LICENSE file has been requested in
[upstream issue #1](https://github.com/g20271/kdeconnect-mcp-server/issues/1);
that issue also offers this SMS work back upstream.

Until it is resolved, treat this fork as **look, don't redistribute**. The SMS
layer added here was written from D-Bus introspection and is offered under the
MIT License by its author.

## 🙏 Acknowledgments

- Built with [FastMCP](https://github.com/jlowin/fastmcp) framework
- Integrates with [KDE Connect](https://kdeconnect.kde.org/)
- Follows the [Model Context Protocol](https://modelcontextprotocol.io/) specification

---

**Made with ❤️ and Claude Code**
