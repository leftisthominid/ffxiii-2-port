#pragma once

// Content availability never depends on the dialogue-language selection.
// Japanese base voices need a merged title-update archive; DLC voice copies
// are independent and may be usable even when the base voice patch is absent.
struct X2VoicePolicy {
  bool use_title_update;
  bool use_merged_update;
  bool use_japanese_base;
  bool request_japanese_dlc;
};
inline X2VoicePolicy X2SelectVoicePolicy(bool japanese, bool title_update,
                                       bool base_voices, bool merged_update,
                                       bool explicit_update) {
  const bool merged = !explicit_update && title_update && japanese &&
                      base_voices && merged_update;
  return {explicit_update || title_update, merged, merged, japanese};
}
