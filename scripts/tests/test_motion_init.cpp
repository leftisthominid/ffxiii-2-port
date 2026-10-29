#include "../../project/src/x2_motion_init.h"
#include <atomic>
#include <barrier>
#include <cassert>
#include <iostream>
#include <thread>

int main() {
  for (bool guarded : {false,true}) {
    std::atomic<unsigned> pointer{0}, allocations{0};
    std::barrier start(2), after_check(2);
    auto native_sequence = [&] {
      if (!pointer.load()) {
        if (!guarded) after_check.arrive_and_wait();
        const auto allocated=allocations.fetch_add(1)+1;
        pointer.store(allocated);
      }
    };
    auto worker = [&] {
      start.arrive_and_wait();
      if (guarded) {
        std::lock_guard<std::mutex> lock(X2MotionInitializationMutex());
        native_sequence();
      } else native_sequence();
    };
    std::thread a(worker),b(worker);a.join();b.join();
    assert(allocations.load()==(guarded?1u:2u));
    assert(pointer.load()!=0);
  }
  std::cout<<"PASS motion initialization: concurrent baseline creates two locks; guarded initialization creates one\n";
}
