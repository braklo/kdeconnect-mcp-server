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
   git clone https://github.com/g20271/kdeconnect-mcp-server.git
   cd kdeconnect-mcp-server
   ```

2. Create virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Verify KDE Connect is running:
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
  cumulative cache instead and polls it until it settles.
- **Partial results are flagged, never hidden.** `list_sms_conversations` and
  `search_sms` report how much they actually covered.

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

### Required permissions

The KDE Connect app on the phone needs **Read SMS** and, for `send_sms`, **Send
SMS**. Without them the conversations plugin returns nothing at all.

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
mcp_server.py (~1200 lines)
├── KDEConnectDBus (290 lines)
│   └── D-Bus interface layer
├── Pydantic Models (80 lines)
│   └── Input validation schemas
└── Tool Definitions (280 lines)
    └── 14 tools with @mcp.tool() decorators
```

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
├── SETUP.md             # Detailed setup guide
└── test_mcp.py          # Test utilities
```

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
sudo apt install python3-dbus python3-gi kdeconnect
```

## 📋 Requirements

### System Requirements
- Linux (Ubuntu 24.04+ recommended)
- KDE Connect 24.x+
- D-Bus

### Python Requirements
- Python 3.12+
- fastmcp >= 1.0.0
- dbus-python >= 1.2.0 (system package)

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
