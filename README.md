# lanshare

Send files directly between two devices on the same Wi-Fi network or mobile
hotspot. No internet connection, no cloud account, no Bluetooth pairing.

One device starts a receiver and gets a one-time PIN. The other device sends
a file to that PIN. That's it. Transfers run over a plain TCP socket at full
local network speed and are encrypted end-to-end, so the exchange is fast
and private even on a network you don't fully trust.

## Why not Bluetooth?

Bluetooth is slow and range-limited. lanshare instead uses whatever Wi-Fi
link is already available - a home router, an office network, or one
phone's hotspot with the other device connected to it - which is typically
10-50x faster and works at normal Wi-Fi range.

## Features

- **Wi-Fi or hotspot, no data plan required** - works the moment two devices
  share any local network, including a hotspot with no upstream internet.
- **Automatic discovery** - senders can find nearby receivers by name
  instead of typing an IP address.
- **End-to-end encryption** - every transfer is protected with AES-256-GCM,
  keyed from a one-time PIN via PBKDF2 (200,000 iterations). The PIN itself
  never crosses the network.
- **Tamper detection** - each file is checksummed with SHA-256 before
  sending and verified again after saving.
- **Path-safe by design** - incoming filenames are sanitized so a malicious
  peer can't write outside the destination folder, and existing files are
  never silently overwritten.
- **Concurrent transfers** - the receiver handles multiple incoming
  connections at once, each on its own thread.
- **Zero configuration** - no accounts, no server, no firewall rules beyond
  the one port lanshare listens on.
- **Simple CLI** - three commands: `receive`, `send`, `discover`.

## Installation

```bash
pip install .
```

or, from a clone of this repository:

```bash
pip install -r requirements.txt
python -m lanshare --help
```

Requires Python 3.8+. The only runtime dependency is the widely used
[`cryptography`](https://pypi.org/project/cryptography/) library.

## Desktop GUI

Prefer a window over a terminal? lanshare ships an optional desktop GUI with
the same Send / Receive / Discover functionality as the CLI.

```bash
pip install "lanshare[gui]"
lanshare gui
```

or run it directly without going through the CLI wrapper:

```bash
lanshare-gui
```

The GUI is built on [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)
and talks to the exact same underlying transfer engine as the command line -
nothing about the protocol, encryption, or discovery changes, it's just a
window instead of a shell.

## Usage

### 1. On the receiving device

```bash
lanshare receive
```

```
lanshare receiver running as 'daniels-laptop'
  Listening on : 192.168.1.42:50556
  Saving to    : /home/daniel/received
  PIN          : 483920

Share the PIN with the sender. Press Ctrl+C to stop.
```

Read that PIN out to whoever is sending you a file (or send it over chat -
it's only valid for this session and is useless without also being on the
same network).

### 2. On the sending device

```bash
lanshare send vacation_photos.zip
```

```
No --host given, searching the network for receivers...
Found daniels-laptop (192.168.1.42:50556)
Enter the PIN shown on the receiving device: 483920
Sending vacation_photos.zip [########################........] 74.2%  186.4 MB/256.0 MB  42.1 MB/s
```

You can also skip discovery and target a device directly:

```bash
lanshare send report.pdf --host 192.168.1.42 --pin 483920
```

Multiple files in one go:

```bash
lanshare send photo1.jpg photo2.jpg notes.txt --host 192.168.1.42
```

### Connecting over a hotspot

If you don't have a shared router, one device can just turn on its mobile
hotspot and the other can connect to it as a normal Wi-Fi network - no data
plan is consumed for the transfer itself since the file never leaves the
local link between the two devices. From there, usage is identical to the
examples above.

### Finding receivers manually

```bash
lanshare discover
```

```
Searching for receivers for 3s...
Found 2 receiver(s):
  - daniels-laptop (192.168.1.42:50556)
  - kitchen-tablet (192.168.1.57:50556)
```

## How it works

1. **Discovery** - the receiver listens for a UDP broadcast asking "who's
   out there?" and answers with its name and port. This never touches file
   contents and can be skipped entirely with `--host`.
2. **Handshake** - the sender generates a random salt and derives a
   256-bit key from the salt and the shared PIN using PBKDF2-HMAC-SHA256.
   The receiver derives the same key from its own copy of the PIN. If the
   PINs don't match, the very first decrypted message fails integrity
   verification and the connection is dropped immediately.
3. **Transfer** - the file is streamed in 1 MiB chunks, each independently
   encrypted and authenticated with AES-256-GCM, so partial data can never
   be tampered with or replayed.
4. **Verification** - the receiver recomputes the SHA-256 checksum of the
   saved file and confirms it matches what the sender sent before reporting
   success.

## Security notes

- The PIN is the only shared secret. Treat it like a temporary password -
  don't post it somewhere public while a transfer is pending.
- Anyone who doesn't know the PIN cannot decrypt a transfer even if they
  can see the network traffic.
- lanshare only ever binds to the local network; it does not open any
  connection to the internet.

## Command reference

| Command | Description |
|---|---|
| `lanshare receive [--out DIR] [--port N] [--pin PIN] [--name NAME]` | Start listening for incoming files |
| `lanshare send FILE [FILE ...] [--host IP] [--port N] [--pin PIN]` | Send one or more files |
| `lanshare discover [--timeout SECONDS]` | List receivers currently visible on the network |

Run `lanshare --help` or `lanshare <command> --help` for the full list of
options.

## Running the tests

```bash
pip install pytest
pytest
```

## License

MIT - see [LICENSE](LICENSE).
