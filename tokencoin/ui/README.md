# TokenCoin Flutter/Electron UI

This directory contains the cross-platform desktop and mobile UI for TokenCoin.

## Architecture

The UI is built with Flutter (for cross-platform desktop + mobile) with a local Rust backend for cryptographic operations via FFI.

```
┌─────────────────────────────────────┐
│         Flutter UI Layer            │
│  ┌─────────┬──────────┬──────────┐  │
│  │Dashboard│  Mining  │ Send/Recv│  │
│  │  Tab    │  Tab     │   Tab    │  │
│  └─────────┴──────────┴──────────┘  │
│  ┌──────────────────────────────┐   │
│  │     Export/Import Tab        │   │
│  └──────────────────────────────┘   │
├─────────────────────────────────────┤
│     WebSocket / IPC Bridge          │
├─────────────────────────────────────┤
│      Python Backend (tokencoin)     │
│  ┌──────┬──────┬──────┬──────────┐  │
│  │Wallet│Mining│Network│Consensus│  │
│  └──────┴──────┴──────┴──────────┘  │
└─────────────────────────────────────┘
```

## Flutter Setup

```bash
# Install Flutter
# https://docs.flutter.dev/get-started/install

# Create the Flutter project
flutter create --org com.tokencoin tokencoin_ui
cd tokencoin_ui

# Add dependencies
flutter pub add web_socket_channel
flutter pub add provider
flutter pub add fl_chart  # For mining visualizations
```

## UI Components

### Dashboard Tab
- Balance display (available, locked, total)
- Recent transaction list
- Network status indicator
- Quick actions (Send, Receive, Mine)

### Mining Tab ("Earn")
- One-click toggle: [Start AI Mining] / [Stop Mining]
- GPU visualization:
  - Temperature gauge
  - VRAM usage bar
  - Model being served
- TKC generation rate chart
- Blocks mined counter
- Total earnings display

### Send/Receive Tab
- Address input with 56-char validation
- Amount input with TKC conversion
- QR code scanner for mobile
- Transaction history

### Export/Import Tab
- Export private key (encrypted)
- Import from mnemonic phrase
- Import from private key hex
- Wallet file management

## WebSocket API

The Python backend exposes a WebSocket API on port 18721:

```json
// Request
{
  "type": "get_balance",
  "id": 1
}

// Response
{
  "type": "balance",
  "id": 1,
  "data": {
    "total": "1234.5678",
    "unlocked": "1200.0000",
    "locked": "34.5678"
  }
}
```

## Building

```bash
# Desktop (macOS)
flutter build macos

# Desktop (Windows)
flutter build windows

# Desktop (Linux)
flutter build linux

# Mobile (Android)
flutter build apk

# Mobile (iOS)
flutter build ios
```
