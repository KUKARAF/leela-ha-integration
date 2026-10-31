# Abandoned approach: fake ESPHome device (notes)

Replaced by the HA custom integration. Findings, in case it is revisited:

- Noise_NNpsk0_25519_ChaChaPoly_SHA256; prologue = "NoiseAPIInit" + BE16(len(client hello)) + client hello (empty hello gives "NoiseAPIInit\0\0").
- Frames: client hello `01 00 00`; server hello `01 len | 01 name\0 mac\0`; handshake `01 len | 00 <noise msg>`; failure `01 len | 01 "Handshake MAC failure"`; transport frame `01 len | AEAD(type:BE16 len:BE16 payload)`. Plaintext: `00 varint(len) varint(type) payload`.
- Message ids: Hello 1/2, Disconnect 5/6, Ping 7/8, DeviceInfo 9/10, ListEntities 11 (+Done 19), SubscribeStates 20, SubscribeHomeassistantServices 34, HomeassistantActionRequest 35, SubscribeHomeAssistantStates 38/39, HomeAssistantStateResponse 40, HomeassistantActionResponse 130.
- Sources: esphome/esphome api_frame_helper_noise.cpp, esphome/aioesphomeapi (_frame_helper/noise.py, api.proto), home-assistant/core esphome/manager.py (service calls honoured only if "Allow the device to perform Home Assistant actions" is on).
- No code was written or run for this approach.
