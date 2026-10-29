// Steam sound effects use the game's software Vorbis path. Its original
// 768 KiB input-buffer heap runs out at about twelve 64 KiB streams, even when
// the decoder and the game's other heaps have memory available. Change both
// the backing allocation and heap extent at the two verified Ogg-init calls.
// builder/patches.ps1 also updates the reader's literal bounds check to 4 MiB.
#pragma once
#include <cstdint>

inline uint32_t X2AudioPoolBytes(uint32_t caller, uint32_t requested) {
  if (requested == 0xC0000 &&
      (caller == 0x82A3287C || caller == 0x82A328CC)) return 4 * 1024 * 1024;
  return requested;
}
