#pragma once
#include <mutex>

// Both native motion initializers use an unguarded check/allocate/publish
// sequence to create their guest critical sections. Serialize that sequence
// so concurrent first users cannot publish different locks for the same data.
inline std::mutex& X2MotionInitializationMutex() {
  static std::mutex mutex;
  return mutex;
}
